# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``apache_php_fpm`` role (design doc §17.7).

The role is library code, so it is imported (``from reemote.roles import
apache_php_fpm``) and replayed against ``SimulatedHost`` through the real wave
driver: ``run_waves(inventory, apache_php_fpm(v), host_factory=...)``. There is
no harness of its own -- the single ``checkpoint`` barrier inside the body *is*
the ``notify: restart apache`` read the upstream handler relied on, and the
driver runs it.

Two assertions carry the migration's claims:

* **convergence** -- run the role, then run it again against the same
  persistent hosts, and the second pass reports no change anywhere. The
  ``files.link`` requests read the ``Link`` fact, so a rerun finds both
  symlinks pointing where they should and yields no command.
* **handler gating** -- the ``Restart apache2 (handler)`` line exists only on
  the pass whose checkpoint found a changed notifying request; on the rerun the
  request is not emitted at all, the handler wave has no records, and the tree
  collapses to wave 0.

The structural claim is pinned too: **one checkpoint, no fact, no act, no
control**. The barrier sequence is exactly ``[CheckpointRequest]``, fresh hosts
run two waves and converged hosts one -- the wave count moves with the host,
which is the point of the barrier model.

Seeding choices, none of them a bridge:

* the host is seeded in the role's *prerequisite* state (D3): apache2
  installed and running, ``mods-available`` and ``mods-enabled`` present, and
  neither proxy module enabled -- exactly a stock Ubuntu host after the
  ``apache`` role's default ``mods_enabled`` pair.
* no between-pass bridge is needed. The ``links``/``link_stats`` stores and
  the ``services`` map are maintained by the simulation's own ``make_link``
  and systemctl mutators, which is why the second pass converges instead of
  churning.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from reemote import run_waves
from reemote.roles import ApachePhpFpmVars, apache_php_fpm
from reemote.rolevars import RoleVarsError, resolve_vars
from tests.simulated_host import SimulatedHost

MODS_AVAILABLE = "/etc/apache2/mods-available"
MODS_ENABLED = "/etc/apache2/mods-enabled"
PROXY_LOAD = f"{MODS_ENABLED}/proxy.load"
PROXY_FCGI_LOAD = f"{MODS_ENABLED}/proxy_fcgi.load"
PROXY_LOAD_NAME = "Enable Apache module proxy.load"
PROXY_FCGI_LOAD_NAME = "Enable Apache module proxy_fcgi.load"
RESTART_NAME = "Restart apache2 (handler)"


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host to be skipped.

    The role variables ride on the entry's ``roles`` mapping under
    ``"apache_php_fpm"``, written with the model's bare field names, which is
    what ``resolve_vars(ApachePhpFpmVars, "apache_php_fpm", inventory)``
    binds, so a test changes a variable exactly the way an operator does.
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
        "roles": {"apache_php_fpm": {}},
    }
    entry["roles"]["apache_php_fpm"].update(overrides)
    return [
        entry,
        {
            "host": "web1",
            "family": "RedHat",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "centos",
        },
    ]


def seed(
    host: SimulatedHost,
    *,
    server_root: str = "/etc/apache2",
    proxy_enabled: bool = False,
    wrong_target: bool = False,
) -> None:
    """Pre-state: a stock Ubuntu host with Apache installed and running (D3).

    ``proxy_enabled`` models the converged host -- both symlinks already
    pointing at their packaged ``mods-available`` files. ``wrong_target``
    models drift: ``proxy.load`` enabled against a stale path, which
    ``files.link`` must re-point (remove + create) and thereby notify.
    """
    mods_available = f"{server_root}/mods-available"
    mods_enabled = f"{server_root}/mods-enabled"
    host.links = {}
    host.link_stats = {}
    if proxy_enabled or wrong_target:
        # proxy_fcgi.load is always the packaged target when seeded; only
        # proxy.load carries the stale-target drift, so the wrong-target case
        # still shows a create next to a re-point.
        stale = "/stale/proxy.load"
        for mod, target in (
            ("proxy.load", stale if wrong_target else f"{mods_available}/proxy.load"),
            ("proxy_fcgi.load", f"{mods_available}/proxy_fcgi.load"),
        ):
            path = f"{mods_enabled}/{mod}"
            host.links[path] = target
            host.link_stats[path] = {
                "mode": 777,
                "user": "root",
                "group": "root",
                "link_target": target,
            }
    host.directory_stats = {
        path: {"mode": 755, "user": "root", "group": "root"}
        for path in (server_root, mods_enabled, mods_available)
    }
    host.services = {"apache2.service": "active"}
    host.enabled_services = {"apache2.service"}


def make_hosts(
    inventory: list[dict[str, Any]], **seed_kwargs: Any
) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded target.

    Only the Ubuntu entry is seeded, so the RedHat host keeps its stock empty
    stores and an accidental request on it is caught by the untouched-store
    assertions.
    """
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            seed(host, **seed_kwargs)
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
    v = resolve_vars(ApachePhpFpmVars, "apache_php_fpm", inventory)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    report, _hosts = await run_waves(
        inventory, apache_php_fpm(v), host_factory=factory, dry=dry
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


def request_kwargs(report: Any, name: str) -> dict:
    """The forwarded keyword arguments of the one planned request called *name*."""
    record = next(
        record
        for record in report.records
        if record.planned is not None and record.planned.request.name == name
    )
    return record.planned.request.kwargs


# --- the first pass really deploys the role -----------------------------------


async def test_first_pass_enables_the_proxy_modules():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # Both symlinks exist and point at their packaged mods-available targets.
    assert host.links[PROXY_LOAD] == f"{MODS_AVAILABLE}/proxy.load"
    assert host.links[PROXY_FCGI_LOAD] == f"{MODS_AVAILABLE}/proxy_fcgi.load"
    assert host.link_stats[PROXY_LOAD]["link_target"] == f"{MODS_AVAILABLE}/proxy.load"

    # The whole changed sequence, in wave order: the two links, then -- because
    # the checkpoint named this host -- the restart handler.
    assert [e.name for e in changed_entries(report)] == [
        PROXY_LOAD_NAME,
        PROXY_FCGI_LOAD_NAME,
        RESTART_NAME,
    ]
    # The links are sudo requests: /etc/apache2 is root-owned. ``sudo`` is an
    # op() meta-parameter, so it lives on the request, not in kwargs or the
    # rendered command.
    link_record = next(
        r
        for r in report.records
        if r.planned is not None and r.planned.request.name == PROXY_LOAD_NAME
    )
    assert link_record.planned.request.sudo


async def test_one_checkpoint_two_waves():
    """The shape: exactly one checkpoint barrier, two waves on a fresh host."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert [type(barrier).__name__ for barrier in barriers(report)] == [
        "CheckpointRequest"
    ]
    assert waves(report) == [0, 1]

    ordered = [entry.name for entry in report.merged().log if entry.host == "ubuntu"]
    assert ordered == [PROXY_LOAD_NAME, PROXY_FCGI_LOAD_NAME, RESTART_NAME]


async def test_drifted_link_is_repointed_and_notifies():
    """A symlink pointing elsewhere is remove + recreate, and it queues the handler.

    ``files.link`` compares the ``Link`` fact's target, so "wrong target" is a
    change -- the faithful form of Ansible's ``force``-less relink semantics on
    a symlink whose destination differs.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory, wrong_target=True)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    assert host.links[PROXY_LOAD] == f"{MODS_AVAILABLE}/proxy.load"
    assert entry_named(report, PROXY_LOAD_NAME).changed
    assert has_entry(report, RESTART_NAME)
    # The other module was seeded correct, so it reports no change -- the
    # notification rides the re-pointed link alone.
    assert not entry_named(report, PROXY_FCGI_LOAD_NAME).changed


# --- the handler gate ----------------------------------------------------------


async def test_handler_only_rides_the_pass_that_changed_a_link():
    """The role's ``notify: restart apache``, as an in-stream checkpoint read.

    On the first pass both links change, so the checkpoint names the host and
    the handler wave carries the restart. On the converged pass nothing
    changed, the checkpoint names nobody, and the restart request is not
    emitted at all -- the handler wave has no records and is not appended.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert has_entry(first, RESTART_NAME)

    second = await run_role(inventory, hosts)
    assert not has_entry(second, RESTART_NAME)
    assert RESTART_NAME not in second.tree()
    assert waves(second) == [0]


async def test_preenabled_host_queues_no_restart():
    """A host that already matches emits no command and notifies nobody.

    Both symlinks are seeded correct, so both requests report no change, the
    checkpoint names nobody, and the whole deploy is two no-change lines.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory, proxy_enabled=True)
    report = await run_role(inventory, hosts)

    assert changed_entries(report) == []
    assert not has_entry(report, RESTART_NAME)


@pytest.mark.parametrize(
    ("restart_state", "handler_name"),
    [
        ("restarted", "Restart apache2 (handler)"),
        ("reloaded", "Reload apache2 (handler)"),
        ("started", "Start apache2 (handler)"),
    ],
)
async def test_restart_state_selects_the_handler_verb(restart_state, handler_name):
    """``apache_restart_state`` from the dependency role, as ``restart_state``."""
    inventory = make_inventory(restart_state=restart_state)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert has_entry(report, handler_name)
    assert handler_name in report.tree()


# --- the variables -------------------------------------------------------------


async def test_server_root_moves_both_paths():
    """``apache_server_root`` builds the two managed symlink paths."""
    inventory = make_inventory(server_root="/opt/httpd")
    hosts = make_hosts(inventory, server_root="/opt/httpd")
    report = await run_role(inventory, hosts)

    assert request_kwargs(report, PROXY_LOAD_NAME)["path"] == "/opt/httpd/mods-enabled/proxy.load"
    assert (
        request_kwargs(report, PROXY_FCGI_LOAD_NAME)["target"]
        == "/opt/httpd/mods-available/proxy_fcgi.load"
    )
    assert hosts["ubuntu"].links["/opt/httpd/mods-enabled/proxy_fcgi.load"] == (
        "/opt/httpd/mods-available/proxy_fcgi.load"
    )


async def test_service_variable_names_the_handler_unit():
    """The unit the restart acts on is a variable, not a hardcoded apache2."""
    inventory = make_inventory(service="httpd")
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert has_entry(report, "Restart httpd (handler)")


def test_restart_state_rejects_other_values():
    with pytest.raises(ValidationError):
        ApachePhpFpmVars(restart_state="retried")


# --- the family guard ----------------------------------------------------------


async def test_non_ubuntu_host_emits_nothing():
    """The family guard is the body's opening ``if``, not a ``when`` on each op.

    A host outside the ``"Ubuntu"`` family returns before it yields anything,
    so it records no request and no barrier -- the Debian ``when`` of the
    upstream task, narrowed to the one target (D1).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    skipped = hosts["web1"]
    assert host_records(report, "web1") == []
    assert [e for e in report.merged().log if e.host == "web1"] == []
    assert skipped.links == {}
    assert skipped.services == {}


# --- convergence ---------------------------------------------------------------


async def test_second_pass_changes_nothing():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    # The checkpoint still closes wave 0 (it is a read of the wave's real
    # outcomes), but it names nobody and the handler wave disappears.
    assert second.tree() == CONVERGED_TREE


async def test_dry_preview_stops_at_the_checkpoint():
    """``dry`` is a one-wave preview: it plans wave 0, parks, and applies nothing.

    The link requests get plan lines, the checkpoint is ``unknown until run``,
    and the body stops there -- no log entry, no command, nothing mutated. A
    converged rerun is the honest preview (§16.6).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    report = await run_role(inventory, hosts, dry=True)

    assert report.merged().log == []
    assert host.links == {}
    assert host.link_stats == {}
    assert report.tree() == DRY_PREVIEW_TREE


# --- the variables model -------------------------------------------------------


def test_vars_are_frozen_and_reject_unknown_fields():
    v = ApachePhpFpmVars()
    with pytest.raises(ValidationError):
        v.server_root = "/opt/httpd"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        # The prefixed inventory spelling is not a field name.
        ApachePhpFpmVars(apache_server_root="/etc/apache2")


def test_inventory_binds_the_prefixed_variables():
    v = resolve_vars(
        ApachePhpFpmVars, "apache_php_fpm", make_inventory(service="httpd")
    )

    assert v["ubuntu"].service == "httpd"
    # The other role's host keeps the defaults: no apache_php_fpm keys bound.
    assert v["web1"] == ApachePhpFpmVars()


def test_typoed_role_variable_is_reported_not_ignored():
    inventory = make_inventory(server_rooto="/opt/httpd")
    make_hosts(inventory)

    with pytest.raises(RoleVarsError, match="server_rooto"):
        resolve_vars(ApachePhpFpmVars, "apache_php_fpm", inventory)


# --- the wave tree (design doc section 7.2) -----------------------------------

# Pinned from a real simulated run. The fresh host is two waves: the symlink
# wave plus its checkpoint, then the handler wave the checkpoint selected. The
# converged rerun is one wave -- the checkpoint names nobody, so the handler
# request does not exist and its wave is never appended.
FIRST_PASS_TREE = """(root)
  wave 0
    ubuntu: Enable Apache module proxy.load [changed: /etc/apache2/mods-enabled/proxy.load missing, will create]
        $ sudo ln -s /etc/apache2/mods-available/proxy.load /etc/apache2/mods-enabled/proxy.load
    ubuntu: Enable Apache module proxy_fcgi.load [changed: /etc/apache2/mods-enabled/proxy_fcgi.load missing, will create]
        $ sudo ln -s /etc/apache2/mods-available/proxy_fcgi.load /etc/apache2/mods-enabled/proxy_fcgi.load
    barrier: configure done → 1 host notified
  wave 1
    ubuntu: Restart apache2 (handler) [changed: apache2.service already running; apache2.service will restart]
        $ sudo systemctl restart apache2.service"""

CONVERGED_TREE = """(root)
  wave 0
    ubuntu: Enable Apache module proxy.load [no change: link /etc/apache2/mods-enabled/proxy.load already exists]
    ubuntu: Enable Apache module proxy_fcgi.load [no change: link /etc/apache2/mods-enabled/proxy_fcgi.load already exists]
    barrier: configure done → 0 hosts notified"""

DRY_PREVIEW_TREE = """(root)
  wave 0
    ubuntu: Enable Apache module proxy.load [changed: /etc/apache2/mods-enabled/proxy.load missing, will create]
    ubuntu: Enable Apache module proxy_fcgi.load [changed: /etc/apache2/mods-enabled/proxy_fcgi.load missing, will create]
    barrier: configure done → unknown until run"""


async def test_tree_snapshot():
    """The whole run as one tree: links, checkpoint, restart handler.

    The second tree is what a rerun prints -- one wave, nothing changed, no
    handler line -- and it is the preview ``dry`` deliberately refuses to fake
    (§16.6).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)

    assert first.tree() == FIRST_PASS_TREE

    second = await run_role(inventory, hosts)
    assert second.tree() == CONVERGED_TREE
