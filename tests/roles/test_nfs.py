# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``nfs`` role (§17.5, §18.14).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, nfs(v), host_factory=...)``. The factory is plain
(non-``async``) -- every input is a resolved variable or a frozen constant --
so it is called, not awaited.

The load-bearing assertion is the migration's shape claim from §18.14: the
stream is **one request wave plus a command-handler barrier** -- the configure
requests, ``checkpoint("exports done")`` closing that wave, then ``act(Shell,
"exportfs -ra")`` for the notified hosts. It is the corpus's only role whose
handler is a command, so the handler wave holds a barrier line and **no request
line**: the request waves stay ``{0}`` on every pass, and the shape test reads
the records straight out of ``report.records``.

The second load-bearing claim is the command-handler mapping. The role's
``reload nfs`` is ``command: exportfs -ra``, not a systemd action, so it rides
the first-class action seam (#16): ``seed()`` plants the canned
``sudo exportfs -ra`` record in ``host.shell_results`` (the exact ``sudo``
rendering is the key) and ``ran_shells`` proves it ran exactly once, on the
notified host only. On the default path (``nfs_exports: []``) the service is
never started, yet the handler still runs -- the case a ``reload_handler`` would
get wrong: ``systemd.service`` acts only on a running unit, so it would
*start* the stopped unit and never run ``exportfs``.

**No between-pass bridge is needed**: the simulation's own mutators maintain
every seam the second pass reads -- ``install_apt_packages`` records the
packages, the ``AptUpdateCommand`` arm refreshes ``apt_update_stamp`` to
wall-clock now (so the folded 600-second cache refresh, deviation D1, skips on
the rerun), ``make_directory`` the export directory rows, ``install_file`` the
exports content and its stat row, and the systemctl arms the service state.
"""

import pytest
from pydantic import ValidationError

from reemote import CommandError, run_waves
from reemote.roles import NfsVars, nfs
from reemote.roles.nfs import (
    APT_CACHE_VALID_TIME,
    EXPORTFS_COMMAND,
    EXPORTS_HEADER,
    EXPORTS_PATH,
    NFS_PACKAGES,
    NFS_SERVER_DAEMON,
    NOTIFYING,
    NfsPlan,
    export_directories,
    nfs_plan,
    render_exports,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from reemote.stages import ActionRequest, CheckpointRequest
from tests.simulated_host import SimulatedHost

#: The exact shell the ``sudo=True`` action renders (no privilege section in the
#: inventory, so a bare ``sudo`` prefix) -- the ``shell_results`` seam key.
HANDLER_SHELL = "sudo exportfs -ra"

#: The role's one handler, named after the Ansible handler it reproduces.
HANDLER_NAME = "reload nfs"

#: ``templates/exports.j2``'s static header, spelled out independently of the
#: role's constant so the render assertion compares against the role's source.
EXPECTED_HEADER = (
    "# /etc/exports: the access control list for filesystems which may be exported\n"
    "#   to NFS clients.  See exports(5).\n"
    "#\n"
    "# Example for NFSv2 and NFSv3:\n"
    "# /srv/homes       hostname1(rw,sync,no_subtree_check) "
    "hostname2(ro,sync,no_subtree_check)\n"
    "#\n"
    "# Example for NFSv4:\n"
    "# /srv/nfs4        gss/krb5i(rw,sync,fsid=0,crossmnt,no_subtree_check)\n"
    "# /srv/nfs4/homes  gss/krb5i(rw,sync,no_subtree_check)\n"
    "#\n"
)

EXPORTS = (
    "/srv/nfs 192.0.2.0/24(rw,sync,no_subtree_check)",
    "/srv/nfs/projects 192.0.2.5(rw,sync,no_subtree_check)",
    "/srv/nfs 192.0.2.6(ro,sync,no_subtree_check)",
)

CONFIGURE_NAMES = {
    "Ensure NFS utilities are installed",
    "Copy exports file",
    "Set exports file mode and owner",
}


def make_inventory(**overrides) -> list[dict]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride as ``nfs_``-prefixed extra keys, bound by
    ``resolve_vars``. With no overrides the role default applies
    (``nfs_exports: []``): the exports file is still written, header-only, and
    the service task stays skipped.
    """
    entry = {
        "host": "ubuntu",
        "family": "Ubuntu",
        "connection": {
            "address": "192.0.2.10",
            "username": "supervisor",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "ubuntu",
        "tags": ["ubuntu_host", "apt_package_manager"],
    }
    entry.update(overrides)
    return [
        entry,
        {
            "host": "web1",
            "family": "RedHat",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "centos",
        },
    ]


def make_two_ubuntu_inventory() -> list[dict]:
    """Two Ubuntu targets plus the skipped RedHat host: the asymmetry case."""
    inventory = make_inventory()
    first = inventory[0]
    second = {
        **first,
        "host": "ubuntu2",
        "connection": {**first["connection"], "address": "192.0.2.12"},
    }
    return [first, second, inventory[1]]


def seed(host: SimulatedHost) -> None:
    """Pre-state: a stock minimal Ubuntu machine with no NFS packages.

    The apt cache has never been refreshed (so the folded 600-second update
    runs), no exports file exists yet, the service is unknown to systemctl, and
    the canned ``exportfs -ra`` answer rides the action seam (#16) keyed by its
    exact ``sudo`` rendering.
    """
    host.apt_packages = {}
    host.apt_sources = []
    host.apt_update_stamp = None
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}
    host.directory_stats = {}
    host.services = {}
    host.enabled_services = set()
    host.shell_results[HANDLER_SHELL] = {
        "exit_status": 0,
        "stdout": "",
        "stderr": "",
    }


def make_hosts(inventory: list[dict]) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            seed(host)
        hosts[host.name] = host
    return hosts


async def run_role(
    inventory: list[dict], hosts: dict[str, SimulatedHost], *, dry: bool = False
):
    """Resolve variables, build the sync factory's deploy, and run one wave pass."""
    v = resolve_vars(NfsVars, "nfs_", inventory)

    def factory(entry: dict) -> SimulatedHost:
        # The *same* host object every pass, so state survives the barriers.
        return hosts[entry["host"]]

    # A plain (non-async) factory: called, not awaited.
    deploy = nfs(v)
    report, _hosts = await run_waves(inventory, deploy, host_factory=factory, dry=dry)
    return report


def changed_entries(report) -> list:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def ubuntu_names(report) -> set[str]:
    """Every Ubuntu log entry name of one pass."""
    return {entry.name for entry in report.merged().log if entry.host == "ubuntu"}


def ubuntu_log(report) -> dict[str, object]:
    """The ubuntu host's log entries of one pass, keyed by name."""
    return {
        entry.name: entry for entry in report.merged().log if entry.host == "ubuntu"
    }


def _kind(barrier) -> tuple:
    """Classify one barrier record for the shape assertion.

    A ``checkpoint`` and an ``act`` both carry a ``name``, so the label pins the
    role's own wording. A ``fact``/``control`` barrier would fall through to a
    distinct label, so a speculatively-added barrier breaks the shape test
    rather than hiding.
    """
    if isinstance(barrier, CheckpointRequest):
        return ("checkpoint", barrier.name)
    if isinstance(barrier, ActionRequest):
        return ("act", barrier.name)
    return ("other", type(barrier).__name__)


def _barrier_shape(report, host: str = "ubuntu"):
    """One host's ordered ``(wave, kind)`` barrier list for a pass."""
    records = [r for r in report.records if r.host == host]
    return [(r.wave, _kind(r.barrier)) for r in records if r.barrier is not None]


def _request_waves(report, host: str = "ubuntu") -> set[int]:
    """The set of wave numbers that carried at least one request for ``host``."""
    return {
        r.wave for r in report.records if r.host == host and r.log is not None
    }


# --- the stream shape (§18.14's falsifier) ------------------------------------


async def test_stream_is_one_checkpoint_and_a_command_handler_wave():
    """§18.14's shape: requests, one checkpoint, then the ``act`` handler.

    Exactly one ``checkpoint`` and exactly one ``act`` barrier, and **no fact or
    control at all** -- the plan is entirely static, so anything else would be a
    barrier added speculatively. The handler wave carries no request, so the
    request waves stay ``{0}`` even on a changing pass: the signature that
    separates this role from every service-handler role in the corpus.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert _barrier_shape(report) == [
        (0, ("checkpoint", "exports done")),
        (1, ("act", HANDLER_NAME)),
    ]
    assert _request_waves(report) == {0}
    assert "exports done → 1 host notified" in report.tree()
    assert f"barrier: {HANDLER_NAME} →" in report.tree()

    # The rerun re-derives the checkpoint, notifies nobody, so the act is gone.
    second = await run_role(inventory, hosts)
    assert _barrier_shape(second) == [(0, ("checkpoint", "exports done"))]
    assert _request_waves(second) == {0}
    assert "exports done → 0 hosts notified" in second.tree()


async def test_only_the_notified_host_yields_the_handler_act():
    """Asymmetric barriers (§10.3): one drifted host, one converged host.

    Both Ubuntu hosts run the same deploy. After a shared first pass both are
    converged, so only the host whose ``/etc/exports`` is corrupted afterwards
    notifies -- and only that host reaches the ``act`` barrier. The other's
    body ends at the checkpoint, so it appears in wave 0 and not wave 1.
    """
    inventory = make_two_ubuntu_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)

    # Both hosts notified on the fresh pass: two act records, one per host.
    assert _barrier_shape(first, "ubuntu") == [
        (0, ("checkpoint", "exports done")),
        (1, ("act", HANDLER_NAME)),
    ]
    assert _barrier_shape(first, "ubuntu2") == [
        (0, ("checkpoint", "exports done")),
        (1, ("act", HANDLER_NAME)),
    ]
    assert hosts["ubuntu"].ran_shells == [HANDLER_SHELL]
    assert hosts["ubuntu2"].ran_shells == [HANDLER_SHELL]

    # Drift only one host's exports file.
    hosts["ubuntu"].remote_files[EXPORTS_PATH] = b"# stale exports\n"

    second = await run_role(inventory, hosts)
    assert _barrier_shape(second, "ubuntu") == [
        (0, ("checkpoint", "exports done")),
        (1, ("act", HANDLER_NAME)),
    ]
    assert _barrier_shape(second, "ubuntu2") == [(0, ("checkpoint", "exports done"))]
    assert hosts["ubuntu"].ran_shells == [HANDLER_SHELL, HANDLER_SHELL]
    assert hosts["ubuntu2"].ran_shells == [HANDLER_SHELL]
    # Only the drifted host changed anything.
    assert {entry.host for entry in changed_entries(second)} == {"ubuntu"}


# --- the first pass really deploys the role ----------------------------------


async def test_first_pass_default_path_installs_and_writes_exports():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    # The Debian packages are installed, with the folded 600-second refresh.
    assert "nfs-common" in host.apt_packages
    assert "nfs-kernel-server" in host.apt_packages
    assert host.apt_update_stamp is not None

    # The exports file: header only (nfs_exports defaults to []), root:root
    # 0644 -- the template task's owner/group/mode.
    assert host.remote_files[EXPORTS_PATH] == EXPECTED_HEADER.encode()
    assert host.file_stats[EXPORTS_PATH] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(EXPECTED_HEADER.encode()),
    }

    # The template task changed, so the command handler fired.
    log = ubuntu_log(report)
    assert log["Copy exports file"].changed
    assert host.ran_shells == [HANDLER_SHELL]


async def test_companion_is_quiet_when_install_wrote_the_metadata():
    """``files.install`` writes content *and* mode, so the companion no-ops.

    The companion's purpose is drift on an otherwise-converged file (the java
    precedent), not a first-pass double write.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert ubuntu_log(report)["Set exports file mode and owner"].changed is False


async def test_default_path_never_starts_the_service():
    """``when: nfs_exports|length > 0`` is frozen to a bool: no request at all."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    assert "Ensure nfs is running" not in ubuntu_names(report)
    assert "Ensure export directory /srv/nfs exists" not in ubuntu_names(report)
    # No export directory row. /etc appears because the spool-and-install write
    # mkdirs the destination's parent, which is the library's own behaviour.
    assert "/srv/nfs" not in host.directory_stats
    assert host.services == {}
    assert host.enabled_services == set()


async def test_non_ubuntu_host_is_never_touched():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    skipped = hosts["web1"]

    # The family guard returns before the first yield, so the off-target host is
    # in no wave, emits no log line, touches no barrier and its stores never move.
    assert not any(entry.host == "web1" for entry in report.merged().log)
    assert not any(r.host == "web1" for r in report.records)
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}
    assert skipped.services == {}
    assert skipped.ran_shells == []


async def test_dry_preview_plans_the_requests_and_parks_at_the_checkpoint():
    """``dry=True`` never runs a barrier, so the handler act stays unplanned.

    The role's first barrier is the checkpoint *after* the request wave, so the
    dry preview plans every configure request and stops there -- the act is never
    built, so nothing even resembles a handler wave.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts, dry=True)

    assert host.apt_packages == {}
    assert host.apt_update_stamp is None
    assert host.remote_files == {}
    assert host.directory_stats == {}
    assert host.services == {}
    assert host.ran_shells == []
    assert changed_entries(report) == []
    assert _barrier_shape(report) == [(0, ("checkpoint", "exports done"))]
    assert _request_waves(report) == set()
    assert report.merged().log == []
    assert "exports done → unknown until run" in report.tree()


async def test_second_pass_is_a_noop_with_no_handler_act():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    # The migration's idempotence claim: nothing changed anywhere and no handler
    # ran -- stamp fresh, packages present, exports content-equal with the right
    # metadata, so nobody notified.
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert _barrier_shape(second) == [(0, ("checkpoint", "exports done"))]
    assert hosts["ubuntu"].ran_shells == [HANDLER_SHELL]


# --- the two notification paths ------------------------------------------------


async def test_metadata_drift_converges_and_notifies():
    """Right content, wrong mode and owner: the companion converges, then notifies.

    ``files.install`` is content-idempotent, so the install request stays quiet;
    the paired ``files.file`` re-applies ``0644 root:root`` -- and its name
    notifies, because Ansible's template reports a change on owner/group/mode
    drift too (D3).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    data = EXPECTED_HEADER.encode()
    host.remote_files[EXPORTS_PATH] = data
    host.existing_files.add(EXPORTS_PATH)
    host.file_stats[EXPORTS_PATH] = {
        "user": "supervisor",
        "group": "supervisor",
        "mode": 600,
        "size": len(data),
    }

    report = await run_role(inventory, hosts)
    log = ubuntu_log(report)

    assert log["Copy exports file"].changed is False
    assert log["Set exports file mode and owner"].changed
    assert host.file_stats[EXPORTS_PATH] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(data),
    }
    # The metadata-only drift still queued the command handler.
    assert host.ran_shells == [HANDLER_SHELL]

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert _barrier_shape(second) == [(0, ("checkpoint", "exports done"))]
    assert host.ran_shells == [HANDLER_SHELL]


async def test_content_drift_reinstalls_and_notifies():
    """Stale content: the install request rewrites the file and queues the reload."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    host.remote_files[EXPORTS_PATH] = b"/srv/old 10.0.0.0/8(ro)\n"
    host.existing_files.add(EXPORTS_PATH)
    host.file_stats[EXPORTS_PATH] = {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(b"/srv/old 10.0.0.0/8(ro)\n"),
    }

    report = await run_role(inventory, hosts)
    log = ubuntu_log(report)

    assert log["Copy exports file"].changed
    assert host.remote_files[EXPORTS_PATH] == EXPECTED_HEADER.encode()
    assert host.ran_shells == [HANDLER_SHELL]

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the configured-exports branch ---------------------------------------------


async def test_exports_create_directories_start_service_and_notify():
    """Non-empty ``nfs_exports``: the directory loop, the render, the service gate."""
    inventory = make_inventory(nfs_exports=list(EXPORTS))
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    # An existing export directory with a non-default mode: the role's mode-less
    # `file: state=directory` task must leave it untouched.
    host.directory_stats["/srv/nfs"] = {"mode": 700, "user": "root", "group": "root"}

    report = await run_role(inventory, hosts)
    names = ubuntu_names(report)

    # map('split') | map('first') | unique: deduplicated, first-occurrence
    # order -- /srv/nfs appears twice in the exports but once in the loop.
    assert "Ensure export directory /srv/nfs exists" in names
    assert "Ensure export directory /srv/nfs/projects exists" in names
    assert host.directory_stats["/srv/nfs"]["mode"] == 700  # untouched
    assert host.directory_stats["/srv/nfs/projects"]["mode"] == 755  # created

    # The rendered exports file: the header plus one line per export.
    assert host.remote_files[EXPORTS_PATH] == (
        EXPECTED_HEADER + "".join(f"{line}\n" for line in EXPORTS)
    ).encode()

    # The service task ran: started and enabled.
    assert "Ensure nfs is running" in names
    assert host.services["nfs-kernel-server.service"] == "active"
    assert "nfs-kernel-server.service" in host.enabled_services

    # The handler fired once for the changed exports file.
    assert host.ran_shells == [HANDLER_SHELL]

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert _barrier_shape(second) == [(0, ("checkpoint", "exports done"))]
    assert host.ran_shells == [HANDLER_SHELL]


async def test_failed_exportfs_raises_command_error():
    """A barrier result is data -- the role turns a non-zero exit into the typed error.

    A silently un-reloaded ``exportfs`` is the one failure this role cannot
    tolerate, so :func:`handler` checks ``exit_status`` itself.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    hosts["ubuntu"].shell_results[HANDLER_SHELL] = {
        "exit_status": 1,
        "stdout": "",
        "stderr": "exportfs: /etc/exports:1: syntax error\n",
    }

    with pytest.raises(CommandError, match=EXPORTFS_COMMAND):
        await run_role(inventory, hosts)


# --- the notification channel ---------------------------------------------------


def test_only_the_exports_requests_notify():
    """The install, directory and service names are deliberately not listeners.

    The role's one ``notify:`` sits on the template task; the metadata companion
    joins it for drift (D3). Nothing else in :data:`NOTIFYING`.
    """
    assert NOTIFYING == frozenset(
        {"Copy exports file", "Set exports file mode and owner"}
    )
    assert "Ensure NFS utilities are installed" not in NOTIFYING
    assert "Ensure nfs is running" not in NOTIFYING
    assert "Ensure export directory /srv/nfs exists" not in NOTIFYING


def test_frozen_constants_are_the_debian_vars_table():
    """``vars/Debian.yml`` and the handler collapsed to constants."""
    assert NFS_SERVER_DAEMON == "nfs-kernel-server"
    assert NFS_PACKAGES == ("nfs-common", "nfs-kernel-server")
    assert EXPORTS_PATH == "/etc/exports"
    assert EXPORTFS_COMMAND == "exportfs -ra"
    assert APT_CACHE_VALID_TIME == 600
    # The header constant is the template's static half, byte for byte: the
    # independently spelled EXPECTED_HEADER is what ``templates/exports.j2``
    # really contains (spaces rather than tabs, "#\n" as its last line).
    assert EXPORTS_HEADER == EXPECTED_HEADER


# --- the pure helpers, unit-tested directly --------------------------------------


def test_render_exports_matches_the_template():
    """The join render is the template source with the loop filled in (D2)."""
    assert render_exports(()) == EXPECTED_HEADER
    assert render_exports(EXPORTS) == EXPECTED_HEADER + "".join(
        f"{line}\n" for line in EXPORTS
    )
    # Every export line is newline-terminated; the header ends with "#\n".
    assert render_exports(("/a x",)).endswith("/a x\n")


def test_export_directories_splits_and_deduplicates():
    assert export_directories(()) == ()
    assert export_directories(EXPORTS) == ("/srv/nfs", "/srv/nfs/projects")
    # Whitespace runs split like Jinja's map('split'); order is first occurrence.
    assert export_directories(("  /b\tx", "/a y", "/b z")) == ("/b", "/a")


def test_nfs_plan_freezes_directories_render_and_gate():
    plan = nfs_plan(NfsVars())
    assert isinstance(plan, NfsPlan)
    assert plan.directories == ()
    assert plan.exports_content == EXPECTED_HEADER
    assert plan.start_service is False

    configured = nfs_plan(NfsVars(exports=EXPORTS))
    assert configured.start_service is True
    assert configured.directories == ("/srv/nfs", "/srv/nfs/projects")
    assert hash(configured) == hash(
        NfsPlan(
            directories=configured.directories,
            exports_content=configured.exports_content,
            start_service=True,
        )
    )


def test_model_is_frozen_and_hashable():
    v = NfsVars()
    assert hash(v) is not None
    assert v.exports == ()
    with pytest.raises(ValidationError):
        v.exports = ("/srv/x y",)


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"nfs_exports": "/srv/nfs x"}, "exports"),
        ({"nfs_exports": [42]}, "exports"),
        ({"nfs_exports": [""]}, "exports"),
        ({"nfs_exports": ["   "]}, "exports"),
    ],
)
def test_invalid_role_values_fail_fast(overrides, match):
    """The old example's ``TypeError``/``ValueError`` gates are pydantic now (D6).

    A bare string is no longer iterable-as-a-list, and a blank entry fails
    before the directory loop could ask Jinja's ``map('first')`` for a path.
    """
    inventory = make_inventory(**overrides)
    with pytest.raises(ValidationError, match=match):
        resolve_vars(NfsVars, "nfs_", inventory)


def test_prefixed_inventory_keys_bind_and_typo_raises():
    """``nfs_`` keys strip onto fields; a prefixed misspelling raises RoleVarsError."""
    inventory = make_inventory(nfs_exports=list(EXPORTS))
    v = resolve_vars(NfsVars, "nfs_", inventory)

    # A JSON list arrives normalized to a hashable tuple (§8.1).
    assert v["ubuntu"].exports == EXPORTS

    with pytest.raises(RoleVarsError, match="nfs_exportss"):
        resolve_vars(NfsVars, "nfs_", make_inventory(nfs_exportss=["/srv/nfs x"]))


async def test_rpcbind_keys_bind_but_change_no_request():
    """The RedHat-only defaults are carried and inert (D5).

    ``setup-RedHat.yml`` is the only thing that reads them, so setting them
    must not alter the Ubuntu stream -- while leaving them *unmodellable* would
    have made an existing inventory raise on an unknown key.
    """
    inventory = make_inventory(nfs_rpcbind_state="stopped", nfs_rpcbind_enabled=False)
    hosts = make_hosts(inventory)
    v = resolve_vars(NfsVars, "nfs_", inventory)
    assert v["ubuntu"].rpcbind_state == "stopped"
    assert v["ubuntu"].rpcbind_enabled is False

    report = await run_role(inventory, hosts)
    assert ubuntu_names(report) == CONFIGURE_NAMES
