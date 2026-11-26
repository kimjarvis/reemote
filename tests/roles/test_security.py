# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``security`` role (design doc section 17.7, T8).

The role is library code now, so it is imported (``from reemote.roles import
security``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, security(v), host_factory=...)``. Ansible's run -- a play
of twenty-odd tasks, three handler notifications and a ``become: true`` -- is one
per-host generator here, and the reads that play took between tasks are barriers
inside it.

Two assertions carry the migration's claims, exactly as in
``tests/roles/test_nginx.py``:

* **convergence** -- run the role, then run it again against the same persistent
  hosts, and the second pass reports no change anywhere and queues no handler.
  Every ``lineinfile`` task is a fixpoint transform of the file's text, so this is
  also the proof that the ten sequential edits of ``/etc/ssh/sshd_config`` compose
  inside one wave rather than trampling each other.
* **handler gating** -- the three ``… (handler)`` lines exist only on the pass
  whose ``checkpoint("configure done")`` found a changed notifying request, and
  each of the three channels is independent: a ``/etc/sudoers`` edit notifies
  nobody, a ``jail.local`` edit reloads only Fail2Ban.

The structural claim is pinned too: **one checkpoint, three ``changed_hosts``
reads**. The whole task list is a single request wave and the handler mechanism is
that one report read three times, so the barrier sequence is exactly
``[CheckpointRequest]`` and the run is two waves changed, one wave converged.

Seeding choices, none of them a bridge:

* ``fail2ban`` and ``unattended-upgrades`` are not installed at the start, and
  ``/etc/fail2ban`` / the two ``apt.conf.d`` files do not exist yet, because the
  simulation's apt install records names rather than payloads.
* ``/etc/ssh/sshd_config`` is seeded with a commented-out ``PasswordAuthentication``
  line and one directive to replace, so the transforms have to do the interesting
  thing (append vs replace) rather than trivially rewrite.
* ``/etc/sudoers`` is seeded root-owned at ``0440``, which the login user cannot
  read -- the shape that makes :func:`~reemote.modules.files.transform` re-read it
  elevated, and the reason a ``visudo -cf {spool}`` failure must abort before the
  file is touched.
* no between-pass bridge is needed at all: every store the role touches (apt's
  package set, ``remote_files``/``file_stats``, the systemd unit state) is
  maintained by the simulation's own mutators, which is why the second pass
  converges instead of churning.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from reemote import run_waves
from reemote.hosts import CommandError
from reemote.modules.files.files import _install_spool_path
from reemote.roles import SecurityVars, security
from reemote.roles.security import (
    ALLOW_GROUPS_NAME,
    ALLOW_USERS_NAME,
    APT_CONF_DIR,
    AUTOUPDATE_NAME,
    FAIL2BAN_LOCAL_NAME,
    FAIL2BAN_LOCAL_PATH,
    JAIL_LOCAL_PATH,
    JAIL_NAME,
    PERIODIC_CONTENT,
    PERIODIC_FILE,
    SSH_CONFIG_PATH,
    SSHD_ITEMS,
    SSHD_MODE_NAME,
    SUDOERS_MODE_NAME,
    SUDOERS_PATH,
    UNATTENDED_FILE,
    daemon_reload_names,
    lineinfile_transform,
    reload_fail2ban_names,
    render_jail_local,
    render_unattended_upgrades,
    resolve_plan,
    restart_ssh_names,
    sshd_edit_name,
    sudoers_edit_name,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from tests.simulated_host import SimulatedHost

#: The two spools the role's ``validate:`` commands run against, so a test can
#: seed one into ``fail_validate`` and drive the abort.
SSHD_SPOOL = _install_spool_path(SSH_CONFIG_PATH)
SUDOERS_SPOOL = _install_spool_path(SUDOERS_PATH)
PERIODIC_PATH = f"{APT_CONF_DIR}/{PERIODIC_FILE}"
UNATTENDED_PATH = f"{APT_CONF_DIR}/{UNATTENDED_FILE}"

#: A stock Ubuntu ``sshd_config``: one commented directive (a ``regexp`` must not
#: match it into a second live line by accident -- it matches, and the transform
#: appends the real directive instead), one directive to replace, and a trailing
#: comment the role never touches.
STOCK_SSHD = (
    b"# Default sshd_config\n"
    b"PermitRootLogin prohibit-password\n"
    b"#PasswordAuthentication yes\n"
    b"UseDNS yes\n"
    b"\n"
    b"# end of file\n"
)
STOCK_SUDOERS = b"root ALL=(ALL:ALL) ALL\n"

#: ``tasks/fail2ban.yml``, in file order.
FAIL2BAN_NAMES = (
    "Install fail2ban (Debian).",
    JAIL_NAME,
    FAIL2BAN_LOCAL_NAME,
    "Ensure fail2ban is running and enabled.",
)
#: ``tasks/ssh.yml``, in file order: the service task, the eight hardening items,
#: the two access items, then the mode request that carries their ``mode: 0644``.
SSH_NAMES = (
    "Ensure SSH daemon is running.",
    *(sshd_edit_name(directive) for directive, _ in SSHD_ITEMS),
    ALLOW_USERS_NAME,
    ALLOW_GROUPS_NAME,
    SSHD_MODE_NAME,
)
#: The two sudoers loops plus their mode request.
SUDOERS_NAMES = (
    sudoers_edit_name("passwordless", "runner"),
    sudoers_edit_name("passworded", "admin"),
    SUDOERS_MODE_NAME,
)
#: ``tasks/autoupdate-Debian.yml``.
AUTOUPDATE_NAMES = (
    "Install unattended upgrades package.",
    f"{AUTOUPDATE_NAME} ({PERIODIC_FILE})",
    f"{AUTOUPDATE_NAME} ({UNATTENDED_FILE})",
)
CONFIGURE_NAMES = FAIL2BAN_NAMES + SSH_NAMES + SUDOERS_NAMES + AUTOUPDATE_NAMES
#: ``handlers/main.yml``'s definition order.
DAEMON_HANDLER = "Reload systemd (handler)"
RESTART_HANDLER = "Restart ssh (handler)"
RELOAD_HANDLER = "Reload fail2ban (handler)"
START_HANDLER = "Start ssh (handler)"


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one RedHat host to be skipped.

    The role variables ride on the entry's ``roles`` mapping under ``"security"``,
    written with the model's bare field names, which is what
    ``resolve_vars(SecurityVars, "security", inventory)`` binds, so a test changes a
    variable exactly the way an operator does. The base entry exercises every
    non-default branch: both access lines, both sudoers loops, the mail and
    blacklist/origins loops of the unattended-upgrades template.
    """
    entry: dict[str, Any] = {
        "host": "ubuntu",
        "family": "Ubuntu",
        "connection": {
            "address": "192.0.2.10",
            "username": "supervisor",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "ubuntu",
        "roles": {
            "security": {
                "ssh_allowed_users": ["deploy"],
                "ssh_allowed_groups": ["sshusers"],
                "sudoers_passwordless": ["runner"],
                "sudoers_passworded": ["admin"],
                "autoupdate_mail_to": "ops@example.com",
                "autoupdate_blacklist": ["nginx"],
                "autoupdate_additional_origins": ["origin=Example"],
            },
        },
    }
    entry["roles"]["security"].update(overrides)
    return [
        entry,
        {
            "host": "web1",
            "family": "RedHat",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "centos",
        },
    ]


def seed(host: SimulatedHost) -> None:
    """Pre-state: a stock Ubuntu machine, neither extra package installed.

    Both protected files exist with the packaged owner and mode; ``sudoers`` is
    ``0440`` and root-owned, so the login user cannot read it and the transform
    has to re-read it elevated.
    """
    host.apt_packages = {}
    host.remote_files = {SSH_CONFIG_PATH: STOCK_SSHD, SUDOERS_PATH: STOCK_SUDOERS}
    host.existing_files = set(host.remote_files)
    host.file_stats = {
        SSH_CONFIG_PATH: {
            "user": "root",
            "group": "root",
            "mode": 644,
            "size": len(STOCK_SSHD),
        },
        SUDOERS_PATH: {
            "user": "root",
            "group": "root",
            "mode": 440,
            "size": len(STOCK_SUDOERS),
        },
    }
    host.directory_stats = {
        path: {"mode": 755, "user": "root", "group": "root"}
        for path in ("/etc/ssh", "/etc/fail2ban", PERIODIC_PATH.rsplit("/", 1)[0])
    }
    host.services = {}
    host.enabled_services = set()


def make_hosts(inventory: list[dict[str, Any]]) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded target.

    Only the Ubuntu entry is seeded, so the RedHat host keeps its stock empty
    stores and an accidental request on it is caught by the untouched-store
    assertions.
    """
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            seed(host)
        hosts[host.name] = host
    return hosts


async def run_role(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SimulatedHost],
    *,
    dry: bool = False,
) -> Any:
    """Resolve the role's variables and run one wave pass against persistent hosts.

    The same ``Host`` objects come back for every entry, so state survives from
    one pass to the next -- which is the whole point of the second run.
    """
    v = resolve_vars(SecurityVars, "security", inventory)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    report, _hosts = await run_waves(
        inventory, security(v), host_factory=factory, dry=dry
    )
    return report


def changed_entries(report: Any) -> list[Any]:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def entry_named(report: Any, name: str, host: str = "ubuntu") -> Any:
    """The one log entry with *name* on *host*."""
    return next(
        entry
        for entry in report.merged().log
        if entry.name == name and entry.host == host
    )


def has_entry(report: Any, name: str, host: str = "ubuntu") -> bool:
    """Whether any log entry with *name* exists on *host*."""
    return any(
        entry.name == name and entry.host == host for entry in report.merged().log
    )


def host_records(report: Any, host: str) -> list[Any]:
    """Every record (request step or barrier) attributed to *host*."""
    return [record for record in report.records if record.host == host]


def barriers(report: Any, host: str = "ubuntu") -> list[Any]:
    """The barrier objects *host* parked on, in wave order."""
    return [record.barrier for record in host_records(report, host) if record.barrier]


def waves(report: Any, host: str = "ubuntu") -> list[int]:
    """The distinct wave numbers *host* emitted records in."""
    return sorted({record.wave for record in host_records(report, host)})


def handler_names(report: Any, host: str = "ubuntu") -> list[str]:
    """The handler requests the checkpoint selected, in yield order."""
    return [
        entry.name
        for entry in report.merged().log
        if entry.host == host and entry.name.endswith("(handler)")
    ]


def request_kwargs(report: Any, name: str) -> dict:
    """The forwarded keyword arguments of the one planned request called *name*."""
    record = next(
        record
        for record in report.records
        if record.planned is not None and record.planned.request.name == name
    )
    return record.planned.request.kwargs


def sshd_text(host: SimulatedHost) -> str:
    """What the role left in ``/etc/ssh/sshd_config``."""
    return host.remote_files[SSH_CONFIG_PATH].decode("utf-8")


def sudoers_text(host: SimulatedHost) -> str:
    """What the role left in ``/etc/sudoers``."""
    return host.remote_files[SUDOERS_PATH].decode("utf-8")


def lines_of(text: str, keyword: str) -> list[str]:
    """The live (uncommented) lines of *text* that start with *keyword*."""
    return [
        line for line in text.splitlines() if line.strip().startswith(keyword)
    ]


# --- the first pass really deploys the role -----------------------------------


async def test_first_pass_hardens_ssh_and_installs_the_packages():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # Both packages installed, neither cache-refreshed (the role has no such task).
    assert "fail2ban" in host.apt_packages
    assert "unattended-upgrades" in host.apt_packages

    # Every hardening directive landed exactly once, as a live line; the seeded
    # comment survived, and the file's other content is untouched.
    sshd = sshd_text(host)
    assert "#PasswordAuthentication yes" in sshd
    assert lines_of(sshd, "PasswordAuthentication") == ["PasswordAuthentication no"]
    assert lines_of(sshd, "Port") == ["Port 22"]
    assert lines_of(sshd, "UseDNS") == ["UseDNS no"]
    for directive, _field in SSHD_ITEMS:
        assert lines_of(sshd, directive), f"{directive} is missing"
    assert "# Default sshd_config" in sshd
    # ``lineinfile`` appends at end of file, so the managed directives land after
    # the seeded trailing comment -- the comment survives, it just is not last.
    assert "# end of file" in sshd.splitlines()
    assert sshd.splitlines()[-1] == "AllowGroups sshusers"

    # Both access lines are the joined lists.
    assert lines_of(sshd, "AllowUsers") == ["AllowUsers deploy"]
    assert lines_of(sshd, "AllowGroups") == ["AllowGroups sshusers"]

    # The sudoers lines, in the file's two-loop order, and the mode preserved.
    sudoers = sudoers_text(host)
    assert "runner ALL=(ALL) NOPASSWD: ALL" in sudoers
    assert "admin ALL=(ALL) ALL" in sudoers
    assert sudoers.startswith("root ALL=(ALL:ALL) ALL\n")
    assert host.file_stats[SUDOERS_PATH]["mode"] == 440
    assert host.file_stats[SSH_CONFIG_PATH]["mode"] == 644

    # The two renders and the two apt files.
    assert host.remote_files[JAIL_LOCAL_PATH].decode() == render_jail_local(
        resolve_vars(SecurityVars, "security", inventory)["ubuntu"]
    )
    assert host.remote_files[FAIL2BAN_LOCAL_PATH].decode() == (
        "[Definition]\nlogtarget = SYSTEMD-JOURNAL\n"
    )
    assert host.remote_files[PERIODIC_PATH].decode() == PERIODIC_CONTENT
    assert host.remote_files[UNATTENDED_PATH].decode() == render_unattended_upgrades(
        resolve_vars(SecurityVars, "security", inventory)["ubuntu"]
    )

    # Services: ssh started (not enabled -- the task sets no ``enabled``),
    # fail2ban started and enabled.
    assert host.services["ssh.service"] == "active"
    assert "ssh.service" not in host.enabled_services
    assert host.services["fail2ban.service"] == "active"
    assert "fail2ban.service" in host.enabled_services

    # All three handlers fired, in definition order.
    assert handler_names(report) == [DAEMON_HANDLER, RESTART_HANDLER, RELOAD_HANDLER]
    assert entry_named(report, DAEMON_HANDLER).changed
    assert entry_named(report, RESTART_HANDLER).changed
    assert entry_named(report, RELOAD_HANDLER).changed

    # The whole changed sequence, in wave order. The two mode requests are the
    # only no-ops (the packaged modes already match).
    assert [e.name for e in changed_entries(report)] == [
        name
        for name in CONFIGURE_NAMES
        if name not in (SSHD_MODE_NAME, SUDOERS_MODE_NAME)
    ] + [DAEMON_HANDLER, RESTART_HANDLER, RELOAD_HANDLER]


async def test_one_checkpoint_three_changed_hosts_reads():
    """One request wave, one checkpoint read three times, one handler wave.

    There is no ``fact``, ``act`` or ``control`` barrier: the Ubuntu path reads
    nothing between tasks. So the barrier sequence is exactly
    ``[CheckpointRequest]`` and the run is two waves.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert [type(b).__name__ for b in barriers(report)] == ["CheckpointRequest"]
    assert waves(report) == [0, 1]

    ordered = [entry.name for entry in report.merged().log if entry.host == "ubuntu"]
    assert ordered == [
        *CONFIGURE_NAMES,
        DAEMON_HANDLER,
        RESTART_HANDLER,
        RELOAD_HANDLER,
    ]
    assert "configure done → 1 host notified" in report.tree()


async def test_the_sshd_edits_compose_in_one_wave():
    """Ten sequential edits of one file, with no barrier between them.

    The driver plans and applies each request in its own step against a fresh fact
    cache, so every transform reads the file as the previous request left it. All
    ten names are in wave 0 -- the checkpoint alone closes it -- and the final
    file holds each directive once, in the loop's order.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    records = host_records(report, "ubuntu")
    edit_names = tuple(sshd_edit_name(directive) for directive, _ in SSHD_ITEMS)
    edit_waves = {
        record.wave
        for record in records
        if record.planned is not None and record.planned.request.name in edit_names
    }
    assert edit_waves == {0}

    sshd = sshd_text(hosts["ubuntu"])
    for directive in (d for d, _ in SSHD_ITEMS):
        assert len(lines_of(sshd, directive)) == 1, f"{directive} appears twice"
    keywords = [line.split()[0] for line in sshd.splitlines() if line.strip()]
    assert keywords.count("Port") == 1
    assert keywords.count("UseDNS") == 1


async def test_sshd_edit_carries_the_validate_hook():
    """``validate: 'sshd -T -f %s'`` is the transform's ``validate`` hook (D2)."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert request_kwargs(report, sshd_edit_name("PasswordAuthentication"))[
        "validate"
    ] == "sshd -T -f {spool}"
    # The validator runs against the spool before the elevated install lands it.
    assert f"sudo sshd -T -f {SSHD_SPOOL}" in report.tree()


async def test_sudoers_edit_reads_and_validates_elevated():
    """A ``0440`` root-owned file: unreadable at login, edited through the spool."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    # The login user cannot read it, so the transform's read-back is elevated.
    assert not host.login_can_read(SUDOERS_PATH)
    assert request_kwargs(report, sudoers_edit_name("passwordless", "runner"))[
        "validate"
    ] == "visudo -cf {spool}"
    assert request_kwargs(report, sudoers_edit_name("passwordless", "runner"))[
        "mode"
    ] == "0440"
    assert f"sudo visudo -cf {SUDOERS_SPOOL}" in report.tree()
    assert f"chmod 440 {SUDOERS_PATH}" in report.tree()


# --- the family guard ---------------------------------------------------------


async def test_non_ubuntu_host_emits_nothing():
    """The family guard is the body's opening ``if``, not a ``when`` on each op.

    A host outside the ``"Ubuntu"`` family returns before it yields anything, so it
    records no request and no barrier -- which is also how the role's
    ``autoupdate-RedHat.yml`` and its EPEL-scoped Fail2Ban install are dropped.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    skipped = hosts["web1"]
    assert host_records(report, "web1") == []
    assert [e for e in report.merged().log if e.host == "web1"] == []
    assert skipped.apt_packages == {}
    assert skipped.remote_files == {}
    assert skipped.services == {}


# --- the three handler channels -----------------------------------------------


async def test_sudoers_change_alone_notifies_nothing():
    """The sudoers loops have no ``notify`` in the role, so they fire no handler.

    Converge the host, then revert only ``/etc/sudoers``. The next pass changes the
    two sudoers edits and nothing else, and the single checkpoint names nobody --
    no handler request, no handler wave.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    host = hosts["ubuntu"]
    host.remote_files[SUDOERS_PATH] = STOCK_SUDOERS
    host.file_stats[SUDOERS_PATH]["size"] = len(STOCK_SUDOERS)

    second = await run_role(inventory, hosts)
    assert [e.name for e in changed_entries(second)] == [
        sudoers_edit_name("passwordless", "runner"),
        sudoers_edit_name("passworded", "admin"),
    ]
    # The checkpoint line counts hosts whose *wave* changed, so it still names this
    # one; what selects the handlers is the three name sets, and none of them
    # contains a sudoers name -- no handler request, no handler wave.
    assert handler_names(second) == []
    assert waves(second) == [0]
    assert "Reload fail2ban (handler)" not in second.tree()


async def test_jail_change_alone_reloads_only_fail2ban():
    """The two Fail2Ban files notify ``reload fail2ban`` and nothing else.

    The hardening channel is disjoint from it, so an sshd restart is not queued for
    a jail edit -- and ``reload systemd`` is not either.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    host = hosts["ubuntu"]
    host.remote_files[JAIL_LOCAL_PATH] = b"[sshd]\nenabled = false\n"
    host.file_stats[JAIL_LOCAL_PATH]["size"] = 24

    second = await run_role(inventory, hosts)
    assert [e.name for e in changed_entries(second)] == [JAIL_NAME, RELOAD_HANDLER]
    assert handler_names(second) == [RELOAD_HANDLER]


async def test_access_lines_ride_the_restart_channel_only():
    """``AllowUsers``/``AllowGroups`` notify ``restart ssh``, never the daemon reload."""
    plan = resolve_plan(
        SecurityVars(ssh_allowed_users=("deploy",), ssh_allowed_groups=("sshusers",))
    )
    assert daemon_reload_names(plan) == (
        *(sshd_edit_name(d) for d, _ in SSHD_ITEMS),
        SSHD_MODE_NAME,
    )
    assert restart_ssh_names(plan) == daemon_reload_names(plan) + (
        ALLOW_USERS_NAME,
        ALLOW_GROUPS_NAME,
    )
    # Neither set mentions the Fail2Ban files or the sudoers edits.
    channels = set(daemon_reload_names(plan)) | set(restart_ssh_names(plan))
    assert channels.isdisjoint(reload_fail2ban_names(plan))
    assert not channels & set(SUDOERS_NAMES)


async def test_no_access_lists_emits_no_access_requests():
    """The two tasks' ``when: … | length > 0`` is a plain empty-list check."""
    inventory = make_inventory(ssh_allowed_users=[], ssh_allowed_groups=[])
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert not has_entry(report, ALLOW_USERS_NAME)
    assert not has_entry(report, ALLOW_GROUPS_NAME)
    assert lines_of(sshd_text(hosts["ubuntu"]), "AllowUsers") == []
    # The restart channel collapses onto the hardening channel.
    plan = resolve_plan(resolve_vars(SecurityVars, "security", inventory)["ubuntu"])
    assert restart_ssh_names(plan) == daemon_reload_names(plan)


async def test_started_handler_state_maps_to_the_running_action():
    """``security_ssh_restart_handler_state: started`` becomes ``action="running"``.

    A ``started`` handler is fact-gated, so on the host the configure wave just
    started it reports no change -- where ``restarted`` (the default) always
    restarts.
    """
    inventory = make_inventory(ssh_restart_handler_state="started")
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert handler_names(report) == [DAEMON_HANDLER, START_HANDLER, RELOAD_HANDLER]
    assert not entry_named(report, START_HANDLER).changed
    assert "systemctl restart ssh.service" not in report.tree()


# --- the gated-off task files -------------------------------------------------


async def test_disabling_both_task_files_leaves_only_ssh_hardening():
    """``security_fail2ban_enabled`` / ``security_autoupdate_enabled`` gate whole files.

    A gated-off ``include_tasks`` emits nothing at all, so neither file's request
    name appears and neither package is touched.
    """
    inventory = make_inventory(fail2ban_enabled=False, autoupdate_enabled=False)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    assert "fail2ban" not in host.apt_packages
    assert "unattended-upgrades" not in host.apt_packages
    assert JAIL_LOCAL_PATH not in host.remote_files
    assert PERIODIC_PATH not in host.remote_files
    for name in (*FAIL2BAN_NAMES, *AUTOUPDATE_NAMES):
        assert not has_entry(report, name)
    assert has_entry(report, sshd_edit_name("Port"))
    # Only the hardening channel is left, so reload fail2ban is not queued.
    assert handler_names(report) == [DAEMON_HANDLER, RESTART_HANDLER]


async def test_stopping_the_daemon_state_stops_the_unit():
    """``security_sshd_state: stopped`` is ``running=False`` on the service task.

    The unit does end up running again, and faithfully so: the same pass changes an
    ``sshd_config`` directive, which notifies ``restart ssh``, and Ansible's
    ``service: state=restarted`` starts a stopped unit. The handler is the role's
    own last word, not the migration's.
    """
    inventory = make_inventory(sshd_state="stopped")
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert request_kwargs(report, "Ensure SSH daemon is running.")["running"] is False
    # The task itself stops the (already stopped) unit and reports no change; the
    # notified restart handler is what brings it back up.
    assert "ssh.service already stopped" in report.tree()
    assert handler_names(report) == [DAEMON_HANDLER, RESTART_HANDLER, RELOAD_HANDLER]
    assert "sudo systemctl start ssh.service" in report.tree()
    assert hosts["ubuntu"].services["ssh.service"] == "active"


# --- convergence --------------------------------------------------------------


async def test_second_pass_changes_nothing():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert second.tree() == CONVERGED_TREE
    # The handler wave disappears entirely: no notified host, no request, no wave.
    assert waves(second) == [0]
    assert handler_names(second) == []


async def test_dry_preview_stops_at_the_configure_checkpoint():
    """``dry`` is a one-wave preview: it plans wave 0, parks, and applies nothing.

    Every configure request gets plan lines, the checkpoint is ``unknown until
    run``, and the body stops there -- so the preview says nothing about which
    handlers would fire. A converged rerun is the honest preview (§16.6).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    report = await run_role(inventory, hosts, dry=True)

    assert report.merged().log == []
    assert host.apt_packages == {}
    assert host.remote_files[SSH_CONFIG_PATH] == STOCK_SSHD
    assert host.remote_files[SUDOERS_PATH] == STOCK_SUDOERS
    assert JAIL_LOCAL_PATH not in host.remote_files
    assert host.services == {}
    assert report.tree() == DRY_PREVIEW_TREE


# --- the validate abort -------------------------------------------------------


async def test_failed_sshd_validation_preserves_the_running_config():
    """A rejected ``sshd -T -f`` never lands: the transform aborts before the ``cp``."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    # The simulated validator fails for this spool, so the apply aborts before the
    # install, leaving the stock config untouched.
    host.fail_validate.add(SSHD_SPOOL)

    with pytest.raises(CommandError):
        await run_role(inventory, hosts)

    assert host.remote_files[SSH_CONFIG_PATH] == STOCK_SSHD
    # The bad config was only staged in the spool, never installed.
    assert SSHD_SPOOL in host.remote_files


async def test_failed_visudo_validation_preserves_the_sudoers_file():
    """The second validator guards the second protected file, the same way."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    host.fail_validate.add(SUDOERS_SPOOL)

    with pytest.raises(CommandError):
        await run_role(inventory, hosts)

    assert host.remote_files[SUDOERS_PATH] == STOCK_SUDOERS


# --- the pure helpers ---------------------------------------------------------


def test_lineinfile_transform_is_a_fixpoint():
    transform = lineinfile_transform("^PasswordAuthentication", "PasswordAuthentication no")
    once = transform("PasswordAuthentication yes\n")
    assert once == "PasswordAuthentication no\n"
    assert transform(once) == once


def test_lineinfile_transform_replaces_the_last_match_only():
    transform = lineinfile_transform("^Port", "Port 2222")
    text = "Port 22\n# Port 8080\nPort 22\nMatch\n"
    assert transform(text) == "Port 22\n# Port 8080\nPort 2222\nMatch\n"


def test_lineinfile_transform_appends_when_nothing_matches():
    transform = lineinfile_transform("^AllowUsers", "AllowUsers deploy")
    assert transform("PermitRootLogin no\n") == "PermitRootLogin no\nAllowUsers deploy\n"


def test_lineinfile_transform_returns_present_content_verbatim():
    """An exact line already present means no write, not a normalized rewrite."""
    transform = lineinfile_transform("^UseDNS", "UseDNS no")
    text = "UseDNS no\n\n# trailing comment\n"
    assert transform(text) == text


def test_lineinfile_transform_creates_an_absent_file():
    """``create: true`` needs no analogue: a transform of ``None`` writes the line."""
    transform = lineinfile_transform("^Port", "Port 22")
    assert transform(None) == "Port 22\n"


def test_jail_local_carries_the_port_and_is_unconditional():
    """``port`` is the string the ``Port`` directive gets; ``backend`` is always set."""
    assert render_jail_local(SecurityVars()) == (
        "[sshd]\nenabled = true\nport    = 22\nfilter  = sshd\nbackend = systemd\n"
    )
    assert "port    = 2222" in render_jail_local(SecurityVars(ssh_port=2222))


def test_unattended_upgrades_origins_are_literal_passthroughs():
    content = render_unattended_upgrades(SecurityVars())
    assert "${distro_codename}" in content
    assert "${distro_id}" in content
    # The loops contribute nothing when unset, and the mail block is absent.
    assert "Mail" not in content
    assert "// Custom repositories\n}" in content
    assert 'Package-Blacklist{\n}' in content


def test_unattended_upgrades_loops_and_mail_block():
    v = SecurityVars(
        autoupdate_mail_to="ops@example.com",
        autoupdate_additional_origins=("origin=Example",),
        autoupdate_blacklist=("nginx",),
    )
    content = render_unattended_upgrades(v)
    assert 'Unattended-Upgrade::Mail "ops@example.com";' in content
    assert 'Unattended-Upgrade::MailOnlyOnError "true";' in content
    assert '    "origin=Example";' in content
    assert '    "nginx";' in content
    assert content.endswith("}\n")


def test_unattended_upgrades_mail_on_error_needs_a_recipient():
    """The inner ``{% if %}`` sits inside the outer one, as the template has it."""
    content = render_unattended_upgrades(
        SecurityVars(autoupdate_mail_on_error=True)
    )
    assert "MailOnlyOnError" not in content


async def test_rendered_content_is_exactly_what_lands():
    """The plan's rendered strings are what the installs write -- no re-render."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    v = resolve_vars(SecurityVars, "security", inventory)["ubuntu"]
    plan = resolve_plan(v)

    await run_role(inventory, hosts)
    assert hosts["ubuntu"].remote_files[UNATTENDED_PATH].decode() == next(
        t.content for t in plan.autoupdate_templates if t.dest == UNATTENDED_PATH
    )


# --- the notification channels ------------------------------------------------


def test_notification_names_are_the_request_names():
    """The channel sets are enumerated from the plan, and every one is a request."""
    plan = resolve_plan(SecurityVars(sudoers_passworded=("admin",)))
    assert daemon_reload_names(plan) == (
        *(sshd_edit_name(d) for d, _ in SSHD_ITEMS),
        SSHD_MODE_NAME,
    )
    assert reload_fail2ban_names(plan) == (JAIL_NAME, FAIL2BAN_LOCAL_NAME)
    # Fail2Ban disabled: the channel is empty, so the handler yields nothing.
    off = resolve_plan(SecurityVars(fail2ban_enabled=False))
    assert reload_fail2ban_names(off) == ()


# --- the variables model ------------------------------------------------------


def test_vars_reject_bad_service_state():
    with pytest.raises(ValidationError, match="sshd_state"):
        SecurityVars(sshd_state="bogus")
    with pytest.raises(ValidationError, match="ssh_restart_handler_state"):
        SecurityVars(ssh_restart_handler_state="stopped")


def test_vars_are_frozen_and_reject_unknown_fields():
    v = SecurityVars()
    with pytest.raises(ValidationError):
        v.ssh_port = "2222"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        # The prefixed inventory spelling is not a field name.
        SecurityVars(security_ssh_port="2222")


def test_sequence_fields_reject_a_bare_string():
    """A string would iterate its characters into the joined line, so it fails."""
    with pytest.raises(ValidationError, match="ssh_allowed_users"):
        SecurityVars(ssh_allowed_users="deploy")


def test_access_members_must_be_one_token():
    with pytest.raises(ValidationError, match="one token"):
        SecurityVars(ssh_allowed_users=("deploy admin",))
    with pytest.raises(ValidationError, match="one token"):
        SecurityVars(ssh_allowed_groups=("",))


def test_sudoers_names_must_be_plain_names():
    for bad in ("a b", "root#x", ""):
        with pytest.raises(ValidationError, match="plain name"):
            SecurityVars(sudoers_passwordless=(bad,))


def test_custom_jail_template_is_rejected_as_scoped_out():
    with pytest.raises(ValidationError, match="jail.local.j2"):
        SecurityVars(fail2ban_custom_configuration_template="myjail.j2")


def test_numbers_coerce_to_strings():
    """``security_ssh_port: 22`` is an int upstream; the line is ``Port 22``."""
    v = SecurityVars(ssh_port=2222)
    assert v.ssh_port == "2222"
    port_edit = next(
        edit for edit in resolve_plan(v).sshd_edits if edit.name.endswith("(Port)")
    )
    assert port_edit.line == "Port 2222"
    assert render_jail_local(v) == (
        "[sshd]\nenabled = true\nport    = 2222\nfilter  = sshd\nbackend = systemd\n"
    )


def test_lists_bind_as_tuples():
    v = resolve_vars(
        SecurityVars,
        "security",
        make_inventory(
            ssh_allowed_users=["deploy", "ops"],
            autoupdate_blacklist=["nginx", "php"],
        ),
    )["ubuntu"]

    assert v.ssh_allowed_users == ("deploy", "ops")
    assert v.autoupdate_blacklist == ("nginx", "php")


def test_inventory_binds_the_prefixed_variables():
    v = resolve_vars(SecurityVars, "security", make_inventory(ssh_usedns="yes"))

    assert v["ubuntu"].ssh_usedns == "yes"
    # The other role's host keeps the defaults: no security_ keys, nothing bound.
    assert v["web1"] == SecurityVars()


def test_typoed_role_variable_is_reported_not_ignored():
    inventory = make_inventory(ssh_usednsr="yes")
    hosts = make_hosts(inventory)

    with pytest.raises(RoleVarsError, match="ssh_usednsr"):
        resolve_vars(SecurityVars, "security", inventory)
    assert hosts["ubuntu"].apt_packages == {}


def test_debian_only_constants_are_not_overridable():
    """``vars/Debian.yml`` sits above an inventory value, so both are frozen (D1)."""
    from reemote.roles.security import SSHD_NAME

    assert SSHD_NAME == "ssh"
    with pytest.raises(RoleVarsError):
        resolve_vars(
            SecurityVars, "security", make_inventory(ssh_config_path="/tmp/sshd_config")
        )


# --- the wave tree (design doc section 7.2) -----------------------------------

# Pinned from a real simulated run. The install spool directories are
# content-addressed and identical for every edit of one file (``fbe6462bc7553466``
# is ``/etc/ssh/sshd_config``), which is the visible proof that the ten edits
# serialize through one spool; the ``configure done`` line is the run's only
# barrier.
FIRST_PASS_TREE = """(root)
  wave 0
    ubuntu: Install fail2ban (Debian). [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install fail2ban
    ubuntu: Copy jail custom configuration file into place. [changed: content /etc/fail2ban/jail.local is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-0efded36b57546fd/content
        $ sudo sh -c 'mkdir -p /etc/fail2ban && cp /tmp/reemote-install-0efded36b57546fd/content /etc/fail2ban/jail.local && chown root:root /etc/fail2ban/jail.local && chmod 644 /etc/fail2ban/jail.local && rm -rf /tmp/reemote-install-0efded36b57546fd'
    ubuntu: Copy fail2ban custom configuration file into place. [changed: content /etc/fail2ban/fail2ban.local is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-3b538d36f3a29155/content
        $ sudo sh -c 'mkdir -p /etc/fail2ban && cp /tmp/reemote-install-3b538d36f3a29155/content /etc/fail2ban/fail2ban.local && chown root:root /etc/fail2ban/fail2ban.local && chmod 644 /etc/fail2ban/fail2ban.local && rm -rf /tmp/reemote-install-3b538d36f3a29155'
    ubuntu: Ensure fail2ban is running and enabled. [changed: fail2ban.service not running (None), will start; fail2ban.service not enabled, will enable]
        $ sudo systemctl start fail2ban.service
        $ sudo systemctl enable fail2ban.service
    ubuntu: Ensure SSH daemon is running. [changed: ssh.service not running (None), will start]
        $ sudo systemctl start ssh.service
    ubuntu: Update SSH configuration to be more secure. (PasswordAuthentication) [changed: content /etc/ssh/sshd_config differs, will install]
        $ scp <files install> -> /tmp/reemote-install-fbe6462bc7553466/content
        $ sudo sshd -T -f /tmp/reemote-install-fbe6462bc7553466/content
        $ sudo sh -c 'mkdir -p /etc/ssh && cp /tmp/reemote-install-fbe6462bc7553466/content /etc/ssh/sshd_config && chmod 644 /etc/ssh/sshd_config && rm -rf /tmp/reemote-install-fbe6462bc7553466'
    ubuntu: Update SSH configuration to be more secure. (PermitRootLogin) [changed: content /etc/ssh/sshd_config differs, will install]
        $ scp <files install> -> /tmp/reemote-install-fbe6462bc7553466/content
        $ sudo sshd -T -f /tmp/reemote-install-fbe6462bc7553466/content
        $ sudo sh -c 'mkdir -p /etc/ssh && cp /tmp/reemote-install-fbe6462bc7553466/content /etc/ssh/sshd_config && chmod 644 /etc/ssh/sshd_config && rm -rf /tmp/reemote-install-fbe6462bc7553466'
    ubuntu: Update SSH configuration to be more secure. (Port) [changed: content /etc/ssh/sshd_config differs, will install]
        $ scp <files install> -> /tmp/reemote-install-fbe6462bc7553466/content
        $ sudo sshd -T -f /tmp/reemote-install-fbe6462bc7553466/content
        $ sudo sh -c 'mkdir -p /etc/ssh && cp /tmp/reemote-install-fbe6462bc7553466/content /etc/ssh/sshd_config && chmod 644 /etc/ssh/sshd_config && rm -rf /tmp/reemote-install-fbe6462bc7553466'
    ubuntu: Update SSH configuration to be more secure. (UseDNS) [changed: content /etc/ssh/sshd_config differs, will install]
        $ scp <files install> -> /tmp/reemote-install-fbe6462bc7553466/content
        $ sudo sshd -T -f /tmp/reemote-install-fbe6462bc7553466/content
        $ sudo sh -c 'mkdir -p /etc/ssh && cp /tmp/reemote-install-fbe6462bc7553466/content /etc/ssh/sshd_config && chmod 644 /etc/ssh/sshd_config && rm -rf /tmp/reemote-install-fbe6462bc7553466'
    ubuntu: Update SSH configuration to be more secure. (PermitEmptyPasswords) [changed: content /etc/ssh/sshd_config differs, will install]
        $ scp <files install> -> /tmp/reemote-install-fbe6462bc7553466/content
        $ sudo sshd -T -f /tmp/reemote-install-fbe6462bc7553466/content
        $ sudo sh -c 'mkdir -p /etc/ssh && cp /tmp/reemote-install-fbe6462bc7553466/content /etc/ssh/sshd_config && chmod 644 /etc/ssh/sshd_config && rm -rf /tmp/reemote-install-fbe6462bc7553466'
    ubuntu: Update SSH configuration to be more secure. (ChallengeResponseAuthentication) [changed: content /etc/ssh/sshd_config differs, will install]
        $ scp <files install> -> /tmp/reemote-install-fbe6462bc7553466/content
        $ sudo sshd -T -f /tmp/reemote-install-fbe6462bc7553466/content
        $ sudo sh -c 'mkdir -p /etc/ssh && cp /tmp/reemote-install-fbe6462bc7553466/content /etc/ssh/sshd_config && chmod 644 /etc/ssh/sshd_config && rm -rf /tmp/reemote-install-fbe6462bc7553466'
    ubuntu: Update SSH configuration to be more secure. (GSSAPIAuthentication) [changed: content /etc/ssh/sshd_config differs, will install]
        $ scp <files install> -> /tmp/reemote-install-fbe6462bc7553466/content
        $ sudo sshd -T -f /tmp/reemote-install-fbe6462bc7553466/content
        $ sudo sh -c 'mkdir -p /etc/ssh && cp /tmp/reemote-install-fbe6462bc7553466/content /etc/ssh/sshd_config && chmod 644 /etc/ssh/sshd_config && rm -rf /tmp/reemote-install-fbe6462bc7553466'
    ubuntu: Update SSH configuration to be more secure. (X11Forwarding) [changed: content /etc/ssh/sshd_config differs, will install]
        $ scp <files install> -> /tmp/reemote-install-fbe6462bc7553466/content
        $ sudo sshd -T -f /tmp/reemote-install-fbe6462bc7553466/content
        $ sudo sh -c 'mkdir -p /etc/ssh && cp /tmp/reemote-install-fbe6462bc7553466/content /etc/ssh/sshd_config && chmod 644 /etc/ssh/sshd_config && rm -rf /tmp/reemote-install-fbe6462bc7553466'
    ubuntu: Add configured users allowed to connect over ssh [changed: content /etc/ssh/sshd_config differs, will install]
        $ scp <files install> -> /tmp/reemote-install-fbe6462bc7553466/content
        $ sudo sshd -T -f /tmp/reemote-install-fbe6462bc7553466/content
        $ sudo sh -c 'mkdir -p /etc/ssh && cp /tmp/reemote-install-fbe6462bc7553466/content /etc/ssh/sshd_config && chmod 644 /etc/ssh/sshd_config && rm -rf /tmp/reemote-install-fbe6462bc7553466'
    ubuntu: Add configured groups allowed to connect over ssh [changed: content /etc/ssh/sshd_config differs, will install]
        $ scp <files install> -> /tmp/reemote-install-fbe6462bc7553466/content
        $ sudo sshd -T -f /tmp/reemote-install-fbe6462bc7553466/content
        $ sudo sh -c 'mkdir -p /etc/ssh && cp /tmp/reemote-install-fbe6462bc7553466/content /etc/ssh/sshd_config && chmod 644 /etc/ssh/sshd_config && rm -rf /tmp/reemote-install-fbe6462bc7553466'
    ubuntu: Set sshd_config mode     [no change: file /etc/ssh/sshd_config already exists]
    ubuntu: Add configured user accounts to passwordless sudoers. (runner) [changed: content /etc/sudoers differs, will install]
        $ scp <files install> -> /tmp/reemote-install-67e88f05f330da88/content
        $ sudo visudo -cf /tmp/reemote-install-67e88f05f330da88/content
        $ sudo sh -c 'mkdir -p /etc && cp /tmp/reemote-install-67e88f05f330da88/content /etc/sudoers && chmod 440 /etc/sudoers && rm -rf /tmp/reemote-install-67e88f05f330da88'
    ubuntu: Add configured user accounts to passworded sudoers. (admin) [changed: content /etc/sudoers differs, will install]
        $ scp <files install> -> /tmp/reemote-install-67e88f05f330da88/content
        $ sudo visudo -cf /tmp/reemote-install-67e88f05f330da88/content
        $ sudo sh -c 'mkdir -p /etc && cp /tmp/reemote-install-67e88f05f330da88/content /etc/sudoers && chmod 440 /etc/sudoers && rm -rf /tmp/reemote-install-67e88f05f330da88'
    ubuntu: Set sudoers mode         [no change: file /etc/sudoers already exists]
    ubuntu: Install unattended upgrades package. [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install unattended-upgrades
    ubuntu: Copy unattended-upgrades configuration files in place. (10periodic) [changed: content /etc/apt/apt.conf.d/10periodic is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-0e019529b29e3308/content
        $ sudo sh -c 'mkdir -p /etc/apt/apt.conf.d && cp /tmp/reemote-install-0e019529b29e3308/content /etc/apt/apt.conf.d/10periodic && chown root:root /etc/apt/apt.conf.d/10periodic && chmod 644 /etc/apt/apt.conf.d/10periodic && rm -rf /tmp/reemote-install-0e019529b29e3308'
    ubuntu: Copy unattended-upgrades configuration files in place. (50unattended-upgrades) [changed: content /etc/apt/apt.conf.d/50unattended-upgrades is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-7c434890d5219277/content
        $ sudo sh -c 'mkdir -p /etc/apt/apt.conf.d && cp /tmp/reemote-install-7c434890d5219277/content /etc/apt/apt.conf.d/50unattended-upgrades && chown root:root /etc/apt/apt.conf.d/50unattended-upgrades && chmod 644 /etc/apt/apt.conf.d/50unattended-upgrades && rm -rf /tmp/reemote-install-7c434890d5219277'
    barrier: configure done → 1 host notified
  wave 1
    ubuntu: Reload systemd (handler) [changed: daemon-reload]
        $ sudo systemctl daemon-reload
    ubuntu: Restart ssh (handler)    [changed: ssh.service already running; ssh.service will restart]
        $ sudo systemctl restart ssh.service
    ubuntu: Reload fail2ban (handler) [changed: fail2ban.service already running; fail2ban.service will reload]
        $ sudo systemctl reload fail2ban.service"""

CONVERGED_TREE = """(root)
  wave 0
    ubuntu: Install fail2ban (Debian). [no change: all packages present]
    ubuntu: Copy jail custom configuration file into place. [no change: content already up to date (/etc/fail2ban/jail.local)]
    ubuntu: Copy fail2ban custom configuration file into place. [no change: content already up to date (/etc/fail2ban/fail2ban.local)]
    ubuntu: Ensure fail2ban is running and enabled. [no change: fail2ban.service already running; fail2ban.service already enabled]
    ubuntu: Ensure SSH daemon is running. [no change: ssh.service already running]
    ubuntu: Update SSH configuration to be more secure. (PasswordAuthentication) [no change: content already up to date (/etc/ssh/sshd_config)]
    ubuntu: Update SSH configuration to be more secure. (PermitRootLogin) [no change: content already up to date (/etc/ssh/sshd_config)]
    ubuntu: Update SSH configuration to be more secure. (Port) [no change: content already up to date (/etc/ssh/sshd_config)]
    ubuntu: Update SSH configuration to be more secure. (UseDNS) [no change: content already up to date (/etc/ssh/sshd_config)]
    ubuntu: Update SSH configuration to be more secure. (PermitEmptyPasswords) [no change: content already up to date (/etc/ssh/sshd_config)]
    ubuntu: Update SSH configuration to be more secure. (ChallengeResponseAuthentication) [no change: content already up to date (/etc/ssh/sshd_config)]
    ubuntu: Update SSH configuration to be more secure. (GSSAPIAuthentication) [no change: content already up to date (/etc/ssh/sshd_config)]
    ubuntu: Update SSH configuration to be more secure. (X11Forwarding) [no change: content already up to date (/etc/ssh/sshd_config)]
    ubuntu: Add configured users allowed to connect over ssh [no change: content already up to date (/etc/ssh/sshd_config)]
    ubuntu: Add configured groups allowed to connect over ssh [no change: content already up to date (/etc/ssh/sshd_config)]
    ubuntu: Set sshd_config mode     [no change: file /etc/ssh/sshd_config already exists]
    ubuntu: Add configured user accounts to passwordless sudoers. (runner) [no change: content already up to date (/etc/sudoers)]
    ubuntu: Add configured user accounts to passworded sudoers. (admin) [no change: content already up to date (/etc/sudoers)]
    ubuntu: Set sudoers mode         [no change: file /etc/sudoers already exists]
    ubuntu: Install unattended upgrades package. [no change: all packages present]
    ubuntu: Copy unattended-upgrades configuration files in place. (10periodic) [no change: content already up to date (/etc/apt/apt.conf.d/10periodic)]
    ubuntu: Copy unattended-upgrades configuration files in place. (50unattended-upgrades) [no change: content already up to date (/etc/apt/apt.conf.d/50unattended-upgrades)]
    barrier: configure done → 0 hosts notified"""

DRY_PREVIEW_TREE = """(root)
  wave 0
    ubuntu: Install fail2ban (Debian). [changed: 1 package(s) to install]
    ubuntu: Copy jail custom configuration file into place. [changed: content /etc/fail2ban/jail.local is absent, will install]
    ubuntu: Copy fail2ban custom configuration file into place. [changed: content /etc/fail2ban/fail2ban.local is absent, will install]
    ubuntu: Ensure fail2ban is running and enabled. [changed: fail2ban.service not running (None), will start; fail2ban.service not enabled, will enable]
    ubuntu: Ensure SSH daemon is running. [changed: ssh.service not running (None), will start]
    ubuntu: Update SSH configuration to be more secure. (PasswordAuthentication) [changed: content /etc/ssh/sshd_config differs, will install]
    ubuntu: Update SSH configuration to be more secure. (PermitRootLogin) [changed: content /etc/ssh/sshd_config differs, will install]
    ubuntu: Update SSH configuration to be more secure. (Port) [changed: content /etc/ssh/sshd_config differs, will install]
    ubuntu: Update SSH configuration to be more secure. (UseDNS) [changed: content /etc/ssh/sshd_config differs, will install]
    ubuntu: Update SSH configuration to be more secure. (PermitEmptyPasswords) [changed: content /etc/ssh/sshd_config differs, will install]
    ubuntu: Update SSH configuration to be more secure. (ChallengeResponseAuthentication) [changed: content /etc/ssh/sshd_config differs, will install]
    ubuntu: Update SSH configuration to be more secure. (GSSAPIAuthentication) [changed: content /etc/ssh/sshd_config differs, will install]
    ubuntu: Update SSH configuration to be more secure. (X11Forwarding) [changed: content /etc/ssh/sshd_config differs, will install]
    ubuntu: Add configured users allowed to connect over ssh [changed: content /etc/ssh/sshd_config differs, will install]
    ubuntu: Add configured groups allowed to connect over ssh [changed: content /etc/ssh/sshd_config differs, will install]
    ubuntu: Set sshd_config mode     [no change: file /etc/ssh/sshd_config already exists]
    ubuntu: Add configured user accounts to passwordless sudoers. (runner) [changed: content /etc/sudoers differs, will install]
    ubuntu: Add configured user accounts to passworded sudoers. (admin) [changed: content /etc/sudoers differs, will install]
    ubuntu: Set sudoers mode         [no change: file /etc/sudoers already exists]
    ubuntu: Install unattended upgrades package. [changed: 1 package(s) to install]
    ubuntu: Copy unattended-upgrades configuration files in place. (10periodic) [changed: content /etc/apt/apt.conf.d/10periodic is absent, will install]
    ubuntu: Copy unattended-upgrades configuration files in place. (50unattended-upgrades) [changed: content /etc/apt/apt.conf.d/50unattended-upgrades is absent, will install]
    barrier: configure done → unknown until run"""


async def test_tree_snapshot():
    """The whole run as one tree: one configure wave, the checkpoint, the handlers.

    The second tree is what a rerun prints -- one wave, no handler line -- and it
    is the preview ``dry`` deliberately refuses to fake (§16.6).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)

    assert first.tree() == FIRST_PASS_TREE

    second = await run_role(inventory, hosts)
    assert second.tree() == CONVERGED_TREE
