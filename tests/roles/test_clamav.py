# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``clamav`` role (design doc §17.7).

The role is library code, so it is imported (``from reemote.roles import
clamav``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, clamav(v), host_factory=...)``. There is no harness of
its own -- the ``checkpoint`` barriers and the ``freshclam`` ``act`` inside
the body *are* the snapshot points the role's ``register``/``notify`` pair
played, and the driver runs them.

Both target families run: the Ubuntu path (the apt trio, ``clamav-daemon`` /
``clamav-freshclam``) and the RedHat path (the dnf quartet, ``clamd@scan``,
and ``setup-RedHat.yml``: the frozen ``clamd-freshclam.service`` unit, its
change-gated reload+stop, the ``/etc/clamd.d/scan.conf`` edits). An Alpine
host proves the family guard -- upstream has no ``vars/Alpine.yml``.

Three assertions carry the migration's claims:

* **convergence** -- run the role, then run it again against the same
  persistent hosts, and the second pass reports no change anywhere. The rerun
  re-derives the freshclam gate, the unit gate and the notification set
  through the checkpoints, so this proves the barriers are honest rather
  than that a frozen plan was reused.
* **the freshclam gate** -- the ``sudo freshclam`` act exists only on the pass
  whose ``checkpoint("packages installed")`` found the install request
  changed; the converged pass never reaches the action seam at all
  (``ran_shells`` stays empty).
* **handler gating** -- the ``Restart <daemon> (handler)`` line exists only on
  the pass whose configure checkpoint found a notifying change (a config
  edit, a mode drift, or the freshclam run itself), and never at all while
  ``daemon_state`` is ``stopped`` (the role's handler ``when:``).

The structural claim is pinned too: **four waves fresh, two converged on
Ubuntu; five fresh, three converged on RedHat** (the extra
``checkpoint("unit installed")`` stays even when nothing fires behind it).
The barrier sequence is ``[checkpoint, act, checkpoint]`` on a fresh Ubuntu
host, ``[checkpoint, act, checkpoint, checkpoint]`` on a fresh RedHat one,
and the freshclam act simply is not appended when nothing gates it.

Seeding choices, none of them a bridge:

* the simulated package installs record package *names*, not payloads, so the
  post-install daemon config the package ships is seeded directly as its
  packaged example content (the simulation's standing convention, as in
  ``tests/roles/test_apache.py``); the transforms then maintain it.
* the ``freshclam`` command rides the action seam: ``shell_results`` keyed by
  the exact ``sudo freshclam`` rendering, with ``ran_shells`` proving how
  often it actually ran.
* the RedHat reload request is decided by ``SystemdNeedDaemonReload``'s own
  ``pending`` over the host's model: a unit file a ``files.install`` wrote
  with *no loaded state modelled* keeps the seam's permissive staleness
  marking (so the fresh install's tree shows ``daemon-reload``), and the
  drift test seeds ``systemd_loaded_fragments`` plus an active unit to prove
  the reload+stop pair against a loaded unit.
* no between-pass bridge is needed. Every store the role touches (package
  rows, file content/stats, systemd unit state) is maintained by the
  simulation's own mutators, which is why the second pass converges.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from reemote import CommandError, run_waves
from reemote.modules import apt, dnf
from reemote.roles import ClamavVars, ConfigChange, clamav
from reemote.roles.clamav import (
    CLAMD_CONF,
    DAEMON_SERVICE_NAME,
    DEBIAN_DAEMON,
    DEBIAN_FRESHCLAM_DAEMON,
    DEBIAN_PACKAGES,
    FRESHCLAM_CONF,
    FRESHCLAM_NAME,
    FRESHCLAM_SERVICE_NAME,
    FRESHCLAM_UNIT,
    FRESHCLAM_UNIT_PATH,
    INSTALL_NAME,
    LOCAL_SOCKET,
    MODE_NAME,
    REDHAT_CLAMD_CONF,
    REDHAT_DAEMON,
    REDHAT_FRESHCLAM_CONF,
    REDHAT_FRESHCLAM_DAEMON,
    REDHAT_LOCAL_SOCKET,
    REDHAT_PACKAGES,
    RELOAD_NAME,
    UNIT_NAME,
    configure_plan,
    daemon_change_name,
    freshclam_change_name,
    lineinfile_absent_transform,
    lineinfile_transform,
    notifying_names,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from reemote.stages import ActionRequest, CheckpointRequest
from tests.simulated_host import SimulatedHost

#: The exact shell the ``sudo=True`` action renders (no privilege section in
#: the inventory, so a bare ``sudo`` prefix) -- the ``shell_results`` seam key.
FRESHCLAM_SHELL = "sudo freshclam"

#: The handler request name ``restart_handler`` generates for the unit.
HANDLER_NAME = "Restart clamav-daemon (handler)"

#: The packaged Debian ``clamd.conf`` (trimmed): the ``Example`` line the
#: first rule drops, a commented ``LocalSocket`` the second rule rewrites.
#: The simulation installs names, not payloads, so this stands in for what
#: the package leaves behind.
CLAMD_SEED = (
    b"Example\n"
    b"#TCPSocket 3310\n"
    b"#TCPAddr 127.0.0.1\n"
    b"# LocalSocket /var/run/clamav/clamd.ctl\n"
)

#: The post-transform content the two default rules converge on.
CLAMD_CONVERGED = (
    b"#TCPSocket 3310\n"
    b"#TCPAddr 127.0.0.1\n"
    b"LocalSocket /var/run/clamav/clamd.ctl\n"
)

EXAMPLE_RE = r"^.*Example$"
SOCKET_RE = r"^.*LocalSocket .*$"

#: The handler request name ``restart_handler`` generates for the RedHat
#: daemon -- a ``clamd@`` template-unit instance.
RH_HANDLER_NAME = "Restart clamd@scan (handler)"

#: The packaged ``scan.conf`` (trimmed), which ``clamav-filesystem`` leaves
#: behind (the ``clamav-scanner-systemd`` spec resolves to it through
#: ``Provides:``): the same two lines the default rules rewrite, in the
#: RedHat spelling. Again names, not payloads.
SCAN_SEED = (
    b"; Generated by clamav-scanner-systemd\n"
    b"Example\n"
    b"# LocalSocket /var/run/clamd.scan/clamd.sock\n"
)

#: The post-transform content the two default rules converge on, RedHat.
SCAN_CONVERGED = (
    b"; Generated by clamav-scanner-systemd\n"
    b"LocalSocket /var/run/clamd.scan/clamd.sock\n"
)

#: Stale unit content for the drift test, so ``files.install`` reports change.
STALE_UNIT = b"[Service]\nExecStart = old\n"

#: What a real EPEL9 machine actually holds once the four upstream names have
#: been requested: ``clamav`` and ``clamav-data`` are RPM names, while
#: ``clamav-update`` and ``clamav-scanner-systemd`` survive only as virtual
#: ``Provides:`` values carried by renamed packages (captured live on the demo
#: hosts -- ``rpm -q --whatprovides`` answered ``clamav-freshclam`` and ``clamd``
#: for them, ``rpm -q`` answered "not installed", issue #76). A converged RedHat
#: host therefore cannot be seeded through the names store alone.
REDHAT_PROVIDERS = ("clamav", "clamav-data", "clamav-freshclam", "clamd")

#: The ``VERSION-RELEASE`` ``RpmProvides`` reports for those providers, verbatim
#: from the live hosts.
REDHAT_RPM_VERSION = "1.4.6-1.el9"

#: Capability -> providing RPM name, the same pairing ``vars/RedHat.yml``'s
#: legacy names still resolve to through ``Provides:``.
REDHAT_CAPABILITIES = {
    "clamav-update": "clamav-freshclam",
    "clamav-scanner-systemd": "clamd",
}


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one Alpine host to be skipped.

    The role variables ride on the entry's ``roles`` mapping under
    ``"clamav"``, written with the model's bare field names, which is what
    ``resolve_vars(ClamavVars, "clamav", inventory)`` binds, so a test changes
    a variable exactly the way an operator does. The base entry exercises the
    defaults path: the Debian package trio, both units started and enabled,
    and the role's two default ``clamd.conf`` rules. The RedHat path runs
    against its own ``rocky`` inventories below; upstream ships no
    ``vars/Alpine.yml``, so Alpine is the off-target family.
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
            "clamav": {
            },
        },
    }
    entry["roles"]["clamav"].update(overrides)
    return [
        entry,
        {
            "host": "web1",
            "family": "Alpine",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "alpine",
        },
    ]


def make_rocky_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A one-host RedHat inventory: the Rocky target, role vars under ``clamav``.

    Same shape as :func:`make_inventory`'s Ubuntu entry, so the RedHat tests
    drive the identical variables path; the unit/config knobs are the family
    table's, not inventory ones (D7).
    """
    return [
        {
            "host": "rocky",
            "family": "RedHat",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "centos",
            "roles": {"clamav": dict(overrides)},
        },
    ]


def seed(
    host: SimulatedHost,
    *,
    preinstalled: bool = False,
    clamd_content: bytes = CLAMD_SEED,
    clamd_mode: int = 644,
) -> None:
    """Pre-state: a stock Ubuntu machine, ClamAV packages optional.

    ``preinstalled`` models the already-packaged case the freshclam gate
    skips: the three names present, so the install wave changes nobody and
    the act barrier is never built. The config file is seeded as the
    package's post-install state (names, not payloads). The canned
    ``sudo freshclam`` answer rides the action seam.
    """
    host.apt_packages = {name: [] for name in DEBIAN_PACKAGES} if preinstalled else {}
    host.apt_update_stamp = None
    host.remote_files = {CLAMD_CONF: clamd_content}
    host.existing_files = {CLAMD_CONF}
    host.file_stats = {
        CLAMD_CONF: {
            "user": "root",
            "group": "root",
            "mode": clamd_mode,
            "size": len(clamd_content),
        }
    }
    host.services = {}
    host.enabled_services = set()
    host.shell_results[FRESHCLAM_SHELL] = {
        "exit_status": 0,
        "stdout": "",
        "stderr": "",
    }


def seed_rocky(
    host: SimulatedHost,
    *,
    preinstalled: bool = False,
    clamd_content: bytes = SCAN_SEED,
    clamd_mode: int = 644,
    unit_drift: bool = False,
) -> None:
    """Pre-state: a stock Rocky machine, ClamAV packages optional.

    The dnf seams are ``host.packages`` (the installed-name store
    ``dnf.packages`` consults first) and ``host.rpm_provides`` (the capability
    rows backing its provides fallback, issue #76): a preinstalled Rocky host
    carries the four provider names and *not* the two virtual ones, which is
    the only way a real EPEL9 machine can answer the request. The RPM's
    ``/etc/clamd.d/scan.conf`` is seeded as its packaged example content, and no
    unit file exists -- ``setup-RedHat.yml`` creates it (D8). ``unit_drift``
    models the machine where the unit was already
    installed, systemd has loaded it, and the loaded state is stale: the
    permissive no-loaded-state marking of the fresh case is replaced by the
    real ``FragmentPath``/pending pair, so the reload reports both halves.
    """
    if preinstalled:
        # The providers, never the capabilities: neither virtual name is an
        # installed RPM name, so only the fallback can settle the request.
        host.packages = {name: [] for name in REDHAT_PROVIDERS}
        host.rpm_provides = [
            {
                "capability": capability,
                "name": provider,
                "version": REDHAT_RPM_VERSION,
            }
            for capability, provider in sorted(REDHAT_CAPABILITIES.items())
        ]
    else:
        host.packages = {}
        host.rpm_provides = []
    host.remote_files = {REDHAT_CLAMD_CONF: clamd_content}
    host.existing_files = {REDHAT_CLAMD_CONF}
    host.file_stats = {
        REDHAT_CLAMD_CONF: {
            "user": "root",
            "group": "root",
            "mode": clamd_mode,
            "size": len(clamd_content),
        }
    }
    host.services = {}
    host.enabled_services = set()
    host.shell_results[FRESHCLAM_SHELL] = {
        "exit_status": 0,
        "stdout": "",
        "stderr": "",
    }
    if unit_drift:
        # A running scanner, a unit whose content drifted, and the loaded
        # state systemd would answer: FragmentPath plus a pending reload.
        host.remote_files[FRESHCLAM_UNIT_PATH] = STALE_UNIT
        host.existing_files.add(FRESHCLAM_UNIT_PATH)
        host.file_stats[FRESHCLAM_UNIT_PATH] = {
            "user": "root",
            "group": "root",
            "mode": 644,
            "size": len(STALE_UNIT),
        }
        host.services["clamd@scan.service"] = "active"
        host.enabled_services.add("clamd@scan.service")
        host.services["clamd-freshclam.service"] = "active"
        host.enabled_services.add("clamd-freshclam.service")
        host.systemd_reload_pending.add("clamd-freshclam.service")
        host.systemd_loaded_fragments["clamd-freshclam.service"] = FRESHCLAM_UNIT_PATH


def make_hosts(inventory: list[dict[str, Any]], **seed_kwargs: Any) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded target.

    The Ubuntu entry takes the Ubuntu seed, the Rocky entry the RedHat seed,
    so an off-target Alpine host keeps its stock empty stores and an
    accidental request on it is caught by the untouched-store assertions.
    """
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            seed(host, **seed_kwargs)
        elif entry.get("family") == "RedHat":
            seed_rocky(host, **seed_kwargs)
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
    v = resolve_vars(ClamavVars, "clamav", inventory)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    report, _hosts = await run_waves(
        inventory, clamav(v), host_factory=factory, dry=dry
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


def barrier_kinds(report: Any, host: str = "ubuntu") -> list[tuple[str, str]]:
    """The ordered ``(kind, name)`` barrier shape of one host's pass."""
    kinds = []
    for barrier in barriers(report, host):
        if isinstance(barrier, CheckpointRequest):
            kinds.append(("checkpoint", barrier.name))
        elif isinstance(barrier, ActionRequest):
            kinds.append(("act", barrier.name))
        else:
            kinds.append(("other", type(barrier).__name__))
    return kinds


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


def clamd_text(host: SimulatedHost) -> bytes:
    """What the role left in ``/etc/clamav/clamd.conf``."""
    return host.remote_files[CLAMD_CONF]


def scan_text(host: SimulatedHost) -> bytes:
    """What the role left in ``/etc/clamd.d/scan.conf``."""
    return host.remote_files[REDHAT_CLAMD_CONF]


# --- the first pass really deploys the role -----------------------------------


async def test_first_pass_installs_configures_and_notifies():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # The three Debian packages, then the gated freshclam run on the action seam.
    assert all(name in host.apt_packages for name in DEBIAN_PACKAGES)
    assert host.ran_shells == [FRESHCLAM_SHELL]

    # Both default rules applied to the packaged example file.
    assert clamd_text(host) == CLAMD_CONVERGED
    assert host.file_stats[CLAMD_CONF]["mode"] == 644

    # Both units are running and enabled under their Debian names.
    assert host.services["clamav-daemon.service"] == "active"
    assert host.services["clamav-freshclam.service"] == "active"
    assert {"clamav-daemon.service", "clamav-freshclam.service"} <= host.enabled_services

    # The changed sequence, in wave order: install (which gates freshclam --
    # a barrier, no log line), both config rules, the service states, and the
    # restart the notification selected. The mode companion did not change.
    assert [e.name for e in changed_entries(report)] == [
        INSTALL_NAME,
        daemon_change_name(EXAMPLE_RE),
        daemon_change_name(SOCKET_RE),
        DAEMON_SERVICE_NAME,
        FRESHCLAM_SERVICE_NAME,
        HANDLER_NAME,
    ]
    assert not entry_named(report, MODE_NAME).changed


async def test_freshclam_gate_only_rides_the_pass_that_installed():
    """The role's ``register: clamav_packages_install`` + ``when: …changed``.

    On the first pass the install changed, so the act barrier ran and the
    configure checkpoint notified through it too. On the converged pass
    nothing installed: the body never reaches the act, ``ran_shells`` stays
    where it was, and the checkpoint names nobody.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert hosts["ubuntu"].ran_shells == [FRESHCLAM_SHELL]
    assert "packages installed → 1 host notified" in first.tree()

    second = await run_role(inventory, hosts)
    assert hosts["ubuntu"].ran_shells == [FRESHCLAM_SHELL]  # no re-run
    assert "packages installed → 0 hosts notified" in second.tree()
    assert not has_entry(second, HANDLER_NAME)


async def test_handler_rides_the_config_channel_without_freshclam():
    """A preinstalled host still restarts on a ``lineinfile`` change.

    The install wave changes nobody (so freshclam never runs), yet the
    packaged example file is still edited: the configure checkpoint notifies
    through the request names alone -- the role's second ``notify`` source.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory, preinstalled=True)
    report = await run_role(inventory, hosts)

    assert hosts["ubuntu"].ran_shells == []
    # The install request ran and reported, but changed nothing.
    assert not entry_named(report, INSTALL_NAME).changed
    changed = [e.name for e in changed_entries(report)]
    assert daemon_change_name(EXAMPLE_RE) in changed
    assert entry_named(report, HANDLER_NAME).changed


async def test_mode_drift_alone_notifies_and_converges():
    """The paired ``files.file`` request: content stays, mode converges (D4).

    A mode-only drift is not a content change, so no transform writes -- but
    the Ansible task asserted ``mode: 0644`` and would have notified on this,
    which is why the mode companion is a notifier.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory, preinstalled=True, clamd_content=CLAMD_CONVERGED, clamd_mode=600)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    assert entry_named(report, MODE_NAME).changed
    assert host.file_stats[CLAMD_CONF]["mode"] == 644
    assert has_entry(report, HANDLER_NAME)

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_one_checkpoint_two_waves_converged():
    """The converged stream: two checkpoints, no act, two waves, no handler."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    second = await run_role(inventory, hosts)
    assert barrier_kinds(second) == [
        ("checkpoint", "packages installed"),
        ("checkpoint", "configure done"),
    ]
    assert waves(second) == [0, 1]
    assert changed_entries(second) == []


# --- the freshclam command barrier (D3) ---------------------------------------


async def test_freshclam_nonzero_exit_raises_command_error():
    """``failed_when`` half one: a non-zero exit aborts, as the play did."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    hosts["ubuntu"].shell_results[FRESHCLAM_SHELL] = {
        "exit_status": 1,
        "stdout": "",
        "stderr": "ERROR: Can't open/parse the config file",
    }
    with pytest.raises(CommandError, match="freshclam"):
        await run_role(inventory, hosts)


async def test_freshclam_lock_message_fails_even_on_zero_exit():
    """``failed_when`` half two, reproduced *literally* (D3).

    The role's condition is an ``any`` of ``is failed`` OR the lock match --
    inverted from the task comment's intent to tolerate the lock -- so even
    an exit-0 run whose stderr carries the message aborts the play.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    hosts["ubuntu"].shell_results[FRESHCLAM_SHELL] = {
        "exit_status": 0,
        "stdout": "",
        "stderr": "ClamD database is locked by another process, waiting...",
    }
    with pytest.raises(CommandError, match="freshclam"):
        await run_role(inventory, hosts)


# --- the variables ------------------------------------------------------------


async def test_packages_override_replaces_the_debian_trio():
    """``clamav_packages`` (the ``set_fact`` fallback) wins over the default."""
    inventory = make_inventory(packages=["clamav", "clamav-daemon"])
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert request_kwargs(report, INSTALL_NAME)["packages"] == (
        "clamav",
        "clamav-daemon",
    )


async def test_daemon_configuration_changes_override_wins_wholesale():
    """An operator list replaces the two default rules, as in Ansible.

    The TCPSocket spelling the role's README documents: the ``Example`` drop
    stays, and the socket rule rewrites the commented ``TCPSocket`` line
    instead of the ``LocalSocket`` one.
    """
    inventory = make_inventory(
        daemon_configuration_changes=[
            {"regexp": EXAMPLE_RE, "state": "absent"},
            {"regexp": r"^.*TCPSocket .*$", "line": "TCPSocket 3310"},
        ]
    )
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # ``lineinfile`` replaces the last regexp match -- the commented
    # ``#TCPSocket 3310`` line becomes the operator's line, and the untouched
    # ``LocalSocket`` comment survives (Ansible would leave it too).
    assert clamd_text(host) == (
        b"TCPSocket 3310\n"
        b"#TCPAddr 127.0.0.1\n"
        b"# LocalSocket /var/run/clamav/clamd.ctl\n"
    )
    assert has_entry(report, daemon_change_name(r"^.*TCPSocket .*$"))
    assert not has_entry(report, daemon_change_name(SOCKET_RE))


async def test_freshclam_changes_apply_but_never_notify():
    """``clamav_freshclam_configuration_changes``: the no-``notify`` loop (D4).

    Undefined by default (the role's ``when: … is defined`` gate), so the base
    path emits nothing; set, the edits ride the configure wave but their
    names stay out of the notification channel -- a converged host whose only
    changed line is a freshclam.conf edit queues no restart.
    """
    inventory = make_inventory(
        freshclam_configuration_changes=[
            {"regexp": r"^.*HTTPProxyServer .*$", "line": "HTTPProxyServer proxy.example"}
        ]
    )
    hosts = make_hosts(inventory, preinstalled=True, clamd_content=CLAMD_CONVERGED)
    # Everything but freshclam.conf already converged, so the freshclam edit is
    # the wave's only change -- and it is not a notifier.
    host = hosts["ubuntu"]
    host.services = {"clamav-daemon.service": "active", "clamav-freshclam.service": "active"}
    host.enabled_services = {"clamav-daemon.service", "clamav-freshclam.service"}
    host.remote_files[FRESHCLAM_CONF] = b"# HTTPProxyServer scanner.example\n"
    host.existing_files.add(FRESHCLAM_CONF)
    host.file_stats[FRESHCLAM_CONF] = {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": 38,
    }
    report = await run_role(inventory, hosts)

    edit = entry_named(report, freshclam_change_name(r"^.*HTTPProxyServer .*$"))
    assert edit.changed
    assert host.remote_files[FRESHCLAM_CONF] == b"HTTPProxyServer proxy.example\n"
    # The only changed entry in the whole pass is the non-notifying edit,
    # and no handler line follows it.
    assert [e.name for e in changed_entries(report)] == [
        freshclam_change_name(r"^.*HTTPProxyServer .*$")
    ]
    assert not has_entry(report, HANDLER_NAME)


async def test_stopped_daemon_skips_the_handler():
    """The handler's ``when: clamav_daemon_state != 'stopped'`` lives in the body (D5).

    With the daemon asked to stay stopped the service task stops/disables it
    (here: touches nothing it never started) and, although the config edits
    notified, the handler wave is never emitted -- the play skipped it the
    same way.
    """
    inventory = make_inventory(daemon_state="stopped")
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert not has_entry(report, HANDLER_NAME)
    assert request_kwargs(report, DAEMON_SERVICE_NAME)["running"] is False
    # freshclam still ran and still "notified" -- the gate is the state, not
    # the notification set.
    assert hosts["ubuntu"].ran_shells == [FRESHCLAM_SHELL]


async def test_restarted_daemon_state_always_reports_change():
    """``state: restarted`` on the task itself: stateless, always changed (D6-adjacent).

    Faithful to Ansible's service module: the verb always reports, and a
    converged rerun of the *role* is no longer quiet -- the operator asked
    for a restart on every run.
    """
    inventory = make_inventory(daemon_state="restarted")
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    entry = entry_named(report, DAEMON_SERVICE_NAME)
    assert entry.changed
    assert request_kwargs(report, DAEMON_SERVICE_NAME)["restarted"] is True


# --- the family guard ---------------------------------------------------------


async def test_off_target_host_emits_nothing():
    """The family guard is the body's opening ``if``, not a ``when`` on each op.

    A host outside the ``"Ubuntu"``/``"RedHat"`` families returns before it
    yields anything, so it records no request and no barrier -- not even the
    RedHat unit file or a dnf request. Alpine is the off-target family because
    upstream has no ``vars/Alpine.yml`` for its ``include_vars`` to load (D1).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    skipped = hosts["web1"]
    assert host_records(report, "web1") == []
    assert [e for e in report.merged().log if e.host == "web1"] == []
    assert skipped.apt_packages == {}
    assert skipped.packages == {}
    assert skipped.remote_files == {}
    assert skipped.services == {}
    assert skipped.ran_shells == []


# --- convergence --------------------------------------------------------------


async def test_second_pass_changes_nothing():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert second.tree() == CONVERGED_TREE


async def test_dry_preview_stops_at_the_install_checkpoint():
    """``dry`` is a one-wave preview: it plans wave 0, parks, and applies nothing.

    The install request gets its plan line, the checkpoint is ``unknown
    until run``, and the body stops there -- the freshclam act, the config
    edits and both unit tasks never appear. A converged rerun is the honest
    preview (§16.6).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    report = await run_role(inventory, hosts, dry=True)

    assert report.merged().log == []
    assert host.apt_packages == {}
    assert host.ran_shells == []
    assert clamd_text(host) == CLAMD_SEED
    assert host.services == {}
    assert report.tree() == DRY_PREVIEW_TREE


# --- the pure helpers ---------------------------------------------------------


def test_absent_transform_drops_matches_and_is_a_fixpoint():
    transform = lineinfile_absent_transform(EXAMPLE_RE)
    assert transform(CLAMD_SEED.decode()) == (
        "#TCPSocket 3310\n#TCPAddr 127.0.0.1\n# LocalSocket /var/run/clamav/clamd.ctl\n"
    )
    once = transform(CLAMD_SEED.decode())
    assert transform(once) == once  # fixpoint
    # A no-match file is returned verbatim, trailing bytes untouched.
    assert transform("keep me\n") == "keep me\n"
    assert transform(None) == ""


def test_present_transform_replaces_last_match_or_appends():
    transform = lineinfile_transform(SOCKET_RE, f"LocalSocket {'/var/run/clamav/clamd.ctl'}")
    result = transform(CLAMD_SEED.decode())
    assert result.splitlines()[-1] == "LocalSocket /var/run/clamav/clamd.ctl"
    assert transform(result) == result  # fixpoint via the exact-line short circuit
    appended = transform("Example\n")
    assert appended == "Example\nLocalSocket /var/run/clamav/clamd.ctl\n"


def test_transform_on_absent_file_creates_content():
    """``create: yes`` is free: ``transform(None)`` produces the file (D4)."""
    transform = lineinfile_transform(r"^Listen ", "Listen 8080")
    assert transform(None) == "Listen 8080\n"


def test_notifying_names_exclude_the_freshclam_and_service_channels():
    """Only the clamd rules and the mode companion queue the restart.

    The freshclam edits carry no ``notify`` in the role and the service tasks
    never do -- the freshclam *command*'s notification is added by the body,
    not by a name (D3).
    """
    plan = configure_plan(ClamavVars(), "Ubuntu")
    names = notifying_names(plan)
    assert names == (
        daemon_change_name(EXAMPLE_RE),
        daemon_change_name(SOCKET_RE),
        MODE_NAME,
    )
    assert DAEMON_SERVICE_NAME not in names
    assert FRESHCLAM_SERVICE_NAME not in names


def test_plan_is_a_pure_function_of_the_vars():
    plan = configure_plan(
        ClamavVars(daemon="clamd", freshclam_daemon="freshclamd"), "Ubuntu"
    )
    assert (plan.daemon, plan.freshclam_daemon) == ("clamd", "freshclamd")
    assert [regexp for regexp, _ in plan.daemon_changes] == [EXAMPLE_RE, SOCKET_RE]
    assert plan.freshclam_changes == ()
    # The transforms are pure and hashable (every ``op()`` argument must hash,
    # design doc section 2.3): equal vars re-derive the same plan, and each
    # closure is a deterministic function with a stable identity hash. The
    # closures differ by identity between calls, so compare around them.
    again = configure_plan(
        ClamavVars(daemon="clamd", freshclam_daemon="freshclamd"), "Ubuntu"
    )
    assert [regexp for regexp, _ in again.daemon_changes] == [
        regexp for regexp, _ in plan.daemon_changes
    ]
    assert (again.daemon_state, again.daemon_enabled) == (
        plan.daemon_state,
        plan.daemon_enabled,
    )
    first = plan.daemon_changes[0][1]
    assert first("Example\nkeep\n") == "keep\n"
    assert first("Example\nkeep\n") == first("Example\nkeep\n")
    assert isinstance(hash(first), int)


# --- the variables model ------------------------------------------------------


def test_vars_are_frozen_and_reject_unknown_fields():
    v = ClamavVars()
    with pytest.raises(ValidationError):
        v.daemon = "clamd"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        # The prefixed inventory spelling is not a field name.
        ClamavVars(clamav_daemon="clamav-daemon")


def test_change_items_validate_state_and_reject_extras():
    assert ConfigChange(regexp="x", state="absent").line == ""
    assert ConfigChange(regexp="x", line="y").state == "present"
    with pytest.raises(ValidationError):
        ConfigChange(regexp="x", state="replaced")
    with pytest.raises(ValidationError):
        ConfigChange(regexp="x", backrefs=True)


def test_inventory_binds_the_prefixed_variables():
    v = resolve_vars(ClamavVars, "clamav", make_inventory(daemon="clamd@scan"))

    assert v["ubuntu"].daemon == "clamd@scan"
    # The other role's host keeps the defaults: no clamav keys, nothing bound.
    assert v["web1"] == ClamavVars()
    # The ``set_fact`` fallbacks are *not* model defaults -- the vars stay
    # unset so the same override can resolve differently per family (D1).
    assert v["web1"].daemon is None
    assert v["web1"].daemon_configuration_changes is None
    assert v["web1"].packages is None


def test_the_families_differ_only_in_their_frozen_tables():
    """``include_vars`` collapsed to :data:`FAMILIES`: one plan, two answers.

    The Ubuntu and RedHat plans are built from the same vars and the same two
    ``lineinfile`` rules -- the notification channel (named per regexp) is
    identical -- while the package manager, the package names, both config
    paths, both unit names and the interpolated localsocket are the family
    table's (D2, D7).
    """
    ubuntu = configure_plan(ClamavVars(), "Ubuntu")
    rocky = configure_plan(ClamavVars(), "RedHat")

    assert ubuntu.installer is apt.packages
    assert rocky.installer is dnf.packages
    assert (ubuntu.packages, rocky.packages) == (DEBIAN_PACKAGES, REDHAT_PACKAGES)
    assert (ubuntu.clamd_conf, rocky.clamd_conf) == (CLAMD_CONF, REDHAT_CLAMD_CONF)
    assert (ubuntu.freshclam_conf, rocky.freshclam_conf) == (
        FRESHCLAM_CONF,
        REDHAT_FRESHCLAM_CONF,
    )
    assert (ubuntu.daemon, rocky.daemon) == (DEBIAN_DAEMON, REDHAT_DAEMON)
    assert (ubuntu.freshclam_daemon, rocky.freshclam_daemon) == (
        DEBIAN_FRESHCLAM_DAEMON,
        REDHAT_FRESHCLAM_DAEMON,
    )
    assert notifying_names(ubuntu) == notifying_names(rocky)
    # Same rule, family's own socket -- the Jinja interpolation froze here.
    assert ubuntu.daemon_changes[1][1](None) == f"LocalSocket {LOCAL_SOCKET}\n"
    assert rocky.daemon_changes[1][1](None) == f"LocalSocket {REDHAT_LOCAL_SOCKET}\n"
    # An operator override beats the table on both sides.
    assert configure_plan(ClamavVars(packages=("clamav",)), "RedHat").packages == (
        "clamav",
    )


def test_typoed_role_variable_is_reported_not_ignored():
    inventory = make_inventory(daemon_stae="stopped")
    hosts = make_hosts(inventory)

    with pytest.raises(RoleVarsError, match="daemon_stae"):
        resolve_vars(ClamavVars, "clamav", inventory)
    # Nothing ran: the failure is at resolve time, before any host is
    # contacted, so both hosts keep their seeded stores untouched.
    assert hosts["ubuntu"].apt_packages == {}
    assert hosts["web1"].apt_packages == {}


# --- the wave tree (design doc section 7.2) -----------------------------------

# Pinned from a real simulated run (the captured output, verbatim). Four waves
# fresh: install + checkpoint, the freshclam act, configure + checkpoint, then
# the handler restart; the converged rerun drops the act and the handler
# waves, keeping the two checkpoint reads.
FIRST_PASS_TREE = """(root)
  wave 0
    ubuntu: Ensure ClamAV packages are installed [changed: 3 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install clamav clamav-base clamav-daemon
    barrier: packages installed → 1 host notified
  wave 1
    barrier: Run freshclam after ClamAV packages change → {'exit_status': 0, 'stdout': '', 'stderr': ''}
  wave 2
    ubuntu: Change configuration for the ClamAV daemon (^.*Example$) [changed: content /etc/clamav/clamd.conf differs, will install]
        $ scp <files install> -> /tmp/reemote-install-ab07f171429272b1/content
        $ sudo sh -c 'mkdir -p /etc/clamav && cp /tmp/reemote-install-ab07f171429272b1/content /etc/clamav/clamd.conf && chmod 644 /etc/clamav/clamd.conf && rm -rf /tmp/reemote-install-ab07f171429272b1'
    ubuntu: Change configuration for the ClamAV daemon (^.*LocalSocket .*$) [changed: content /etc/clamav/clamd.conf differs, will install]
        $ scp <files install> -> /tmp/reemote-install-ab07f171429272b1/content
        $ sudo sh -c 'mkdir -p /etc/clamav && cp /tmp/reemote-install-ab07f171429272b1/content /etc/clamav/clamd.conf && chmod 644 /etc/clamav/clamd.conf && rm -rf /tmp/reemote-install-ab07f171429272b1'
    ubuntu: Set ClamAV daemon configuration mode [no change: file /etc/clamav/clamd.conf already exists]
    ubuntu: Ensure ClamAV daemon is running (if configured) [changed: clamav-daemon.service not running (None), will start; clamav-daemon.service not enabled, will enable]
        $ sudo systemctl start clamav-daemon.service
        $ sudo systemctl enable clamav-daemon.service
    ubuntu: Ensure ClamAV freshclam daemon is running (if configured) [changed: clamav-freshclam.service not running (None), will start; clamav-freshclam.service not enabled, will enable]
        $ sudo systemctl start clamav-freshclam.service
        $ sudo systemctl enable clamav-freshclam.service
    barrier: configure done → 1 host notified
  wave 3
    ubuntu: Restart clamav-daemon (handler) [changed: clamav-daemon.service already running; clamav-daemon.service will restart]
        $ sudo systemctl restart clamav-daemon.service"""

CONVERGED_TREE = """(root)
  wave 0
    ubuntu: Ensure ClamAV packages are installed [no change: all packages present]
    barrier: packages installed → 0 hosts notified
  wave 1
    ubuntu: Change configuration for the ClamAV daemon (^.*Example$) [no change: content already up to date (/etc/clamav/clamd.conf)]
    ubuntu: Change configuration for the ClamAV daemon (^.*LocalSocket .*$) [no change: content already up to date (/etc/clamav/clamd.conf)]
    ubuntu: Set ClamAV daemon configuration mode [no change: file /etc/clamav/clamd.conf already exists]
    ubuntu: Ensure ClamAV daemon is running (if configured) [no change: clamav-daemon.service already running; clamav-daemon.service already enabled]
    ubuntu: Ensure ClamAV freshclam daemon is running (if configured) [no change: clamav-freshclam.service already running; clamav-freshclam.service already enabled]
    barrier: configure done → 0 hosts notified"""

DRY_PREVIEW_TREE = """(root)
  wave 0
    ubuntu: Ensure ClamAV packages are installed [changed: 3 package(s) to install]
    barrier: packages installed → unknown until run"""


async def test_tree_snapshot():
    """The whole run as one tree: install, freshclam, configure, handler.

    The second tree is what a rerun prints -- two waves, two checkpoints,
    nothing changed and no handler line -- and it is the preview ``dry``
    deliberately refuses to fake (§16.6).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)

    assert first.tree() == FIRST_PASS_TREE
    assert barrier_kinds(first) == [
        ("checkpoint", "packages installed"),
        ("act", FRESHCLAM_NAME),
        ("checkpoint", "configure done"),
    ]
    assert waves(first) == [0, 1, 2, 3]

    second = await run_role(inventory, hosts)
    assert second.tree() == CONVERGED_TREE


# --- the RedHat path (setup-RedHat.yml, D8) -----------------------------------

# Pinned from a real simulated run (the captured output, verbatim). Five waves
# fresh: install + checkpoint, the freshclam act, the unit + its checkpoint,
# the gated reload with the configure wave + checkpoint, then the handler.
RH_FIRST_PASS_TREE = """(root)
  wave 0
    rocky: Ensure ClamAV packages are installed [changed: 4 package(s) to install]
        $ sudo dnf install -y clamav clamav-update clamav-scanner-systemd clamav-data
    barrier: packages installed → 1 host notified
  wave 1
    barrier: Run freshclam after ClamAV packages change → {'exit_status': 0, 'stdout': '', 'stderr': ''}
  wave 2
    rocky: Ensure Freshclam service is available [changed: content /lib/systemd/system/clamd-freshclam.service is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-64eeb2c58dd82b68/content
        $ sudo sh -c 'mkdir -p /lib/systemd/system && cp /tmp/reemote-install-64eeb2c58dd82b68/content /lib/systemd/system/clamd-freshclam.service && chown root:root /lib/systemd/system/clamd-freshclam.service && chmod 644 /lib/systemd/system/clamd-freshclam.service && rm -rf /tmp/reemote-install-64eeb2c58dd82b68'
    barrier: unit installed → 1 host notified
  wave 3
    rocky: Reload systemd after adding service [changed: daemon-reload; clamd-freshclam.service already stopped]
        $ sudo systemctl daemon-reload
    rocky: Change configuration for the ClamAV daemon (^.*Example$) [changed: content /etc/clamd.d/scan.conf differs, will install]
        $ scp <files install> -> /tmp/reemote-install-9a2f6baaa308cba3/content
        $ sudo sh -c 'mkdir -p /etc/clamd.d && cp /tmp/reemote-install-9a2f6baaa308cba3/content /etc/clamd.d/scan.conf && chmod 644 /etc/clamd.d/scan.conf && rm -rf /tmp/reemote-install-9a2f6baaa308cba3'
    rocky: Change configuration for the ClamAV daemon (^.*LocalSocket .*$) [changed: content /etc/clamd.d/scan.conf differs, will install]
        $ scp <files install> -> /tmp/reemote-install-9a2f6baaa308cba3/content
        $ sudo sh -c 'mkdir -p /etc/clamd.d && cp /tmp/reemote-install-9a2f6baaa308cba3/content /etc/clamd.d/scan.conf && chmod 644 /etc/clamd.d/scan.conf && rm -rf /tmp/reemote-install-9a2f6baaa308cba3'
    rocky: Set ClamAV daemon configuration mode [no change: file /etc/clamd.d/scan.conf already exists]
    rocky: Ensure ClamAV daemon is running (if configured) [changed: clamd@scan.service not running (None), will start; clamd@scan.service not enabled, will enable]
        $ sudo systemctl start clamd@scan.service
        $ sudo systemctl enable clamd@scan.service
    rocky: Ensure ClamAV freshclam daemon is running (if configured) [changed: clamd-freshclam.service not running (None), will start; clamd-freshclam.service not enabled, will enable]
        $ sudo systemctl start clamd-freshclam.service
        $ sudo systemctl enable clamd-freshclam.service
    barrier: configure done → 1 host notified
  wave 4
    rocky: Restart clamd@scan (handler) [changed: clamd@scan.service already running; clamd@scan.service will restart]
        $ sudo systemctl restart clamd@scan.service"""

# Three waves converged: the unit file's request and its checkpoint stay (an
# empty wave is never appended, but this one is not empty), while nothing
# behind the unit checkpoint fires any more.
RH_CONVERGED_TREE = """(root)
  wave 0
    rocky: Ensure ClamAV packages are installed [no change: all packages present]
    barrier: packages installed → 0 hosts notified
  wave 1
    rocky: Ensure Freshclam service is available [no change: content already up to date (/lib/systemd/system/clamd-freshclam.service)]
    barrier: unit installed → 0 hosts notified
  wave 2
    rocky: Change configuration for the ClamAV daemon (^.*Example$) [no change: content already up to date (/etc/clamd.d/scan.conf)]
    rocky: Change configuration for the ClamAV daemon (^.*LocalSocket .*$) [no change: content already up to date (/etc/clamd.d/scan.conf)]
    rocky: Set ClamAV daemon configuration mode [no change: file /etc/clamd.d/scan.conf already exists]
    rocky: Ensure ClamAV daemon is running (if configured) [no change: clamd@scan.service already running; clamd@scan.service already enabled]
    rocky: Ensure ClamAV freshclam daemon is running (if configured) [no change: clamd-freshclam.service already running; clamd-freshclam.service already enabled]
    barrier: configure done → 0 hosts notified"""

RH_DRY_TREE = """(root)
  wave 0
    rocky: Ensure ClamAV packages are installed [changed: 4 package(s) to install]
    barrier: packages installed → unknown until run"""


async def test_rocky_first_pass_installs_the_unit_and_notifies():
    """``setup-RedHat.yml`` sits between the freshclam gate and the edits.

    The dnf quartet, the frozen unit file, the gated reload, the family's own
    config path and the ``clamd@scan`` restart the notification selected.
    """
    inventory = make_rocky_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["rocky"]

    # The family's manager and package set, then the gated freshclam run.
    assert all(name in host.packages for name in REDHAT_PACKAGES)
    assert host.ran_shells == [FRESHCLAM_SHELL]

    # The unit file landed verbatim (no renderer), root-owned at 0644 (D8).
    assert host.remote_files[FRESHCLAM_UNIT_PATH] == FRESHCLAM_UNIT.encode()
    assert host.file_stats[FRESHCLAM_UNIT_PATH]["mode"] == 644
    assert host.file_stats[FRESHCLAM_UNIT_PATH]["user"] == "root"

    # The RedHat config path with the RedHat socket; no Debian path touched (D7).
    assert scan_text(host) == SCAN_CONVERGED
    assert CLAMD_CONF not in host.remote_files
    assert FRESHCLAM_CONF not in host.remote_files

    # Both units, under their RedHat names -- the template-instance included.
    assert host.services["clamd@scan.service"] == "active"
    assert host.services["clamd-freshclam.service"] == "active"
    assert {"clamd@scan.service", "clamd-freshclam.service"} <= host.enabled_services

    assert [e.name for e in changed_entries(report)] == [
        INSTALL_NAME,
        UNIT_NAME,
        RELOAD_NAME,
        daemon_change_name(EXAMPLE_RE),
        daemon_change_name(SOCKET_RE),
        DAEMON_SERVICE_NAME,
        FRESHCLAM_SERVICE_NAME,
        RH_HANDLER_NAME,
    ]
    assert not entry_named(report, MODE_NAME, "rocky").changed


async def test_rocky_tree_snapshot():
    """Five waves fresh, three converged -- the extra checkpoint stays empty-run."""
    inventory = make_rocky_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)

    assert first.tree() == RH_FIRST_PASS_TREE
    assert barrier_kinds(first, "rocky") == [
        ("checkpoint", "packages installed"),
        ("act", FRESHCLAM_NAME),
        ("checkpoint", "unit installed"),
        ("checkpoint", "configure done"),
    ]
    assert waves(first, "rocky") == [0, 1, 2, 3, 4]

    second = await run_role(inventory, hosts)
    assert second.tree() == RH_CONVERGED_TREE
    assert waves(second, "rocky") == [0, 1, 2]
    assert changed_entries(second) == []


async def test_rocky_reload_rides_only_the_unit_change():
    """The role's ``register: freshclam_service_template`` + ``when: …changed``.

    The first pass wrote the unit, so the reload request ran; on the converged
    rerun the checkpoint names nobody and the request is never built -- no log
    line at all, not a no-change line (D8).
    """
    inventory = make_rocky_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)

    assert entry_named(first, RELOAD_NAME, "rocky").changed
    assert "unit installed → 1 host notified" in first.tree()

    second = await run_role(inventory, hosts)
    assert not has_entry(second, RELOAD_NAME, "rocky")
    assert "unit installed → 0 hosts notified" in second.tree()


async def test_rocky_preinstalled_skips_freshclam_but_still_installs_the_unit():
    """Two independent gates: the install checkpoint and the unit checkpoint.

    Nothing installed, so no ``sudo freshclam`` and one wave less; the unit is
    still new, so its reload and the notified restart both ride on.
    """
    inventory = make_rocky_inventory()
    hosts = make_hosts(inventory, preinstalled=True)
    host = hosts["rocky"]
    report = await run_role(inventory, hosts)

    # The seed is a real EPEL9 shape: the two capability names are installed by
    # nothing, so the request settles only through ``dnf.packages``' provides
    # fallback -- and it still reports no change, leaving no freshclam wave.
    assert not {"clamav-update", "clamav-scanner-systemd"} & set(host.packages)
    assert sorted(row["capability"] for row in host.rpm_provides) == [
        "clamav-scanner-systemd",
        "clamav-update",
    ]
    assert hosts["rocky"].ran_shells == []
    assert not entry_named(report, INSTALL_NAME, "rocky").changed
    assert entry_named(report, UNIT_NAME, "rocky").changed
    assert has_entry(report, RELOAD_NAME, "rocky")
    assert barrier_kinds(report, "rocky") == [
        ("checkpoint", "packages installed"),
        ("checkpoint", "unit installed"),
        ("checkpoint", "configure done"),
    ]
    assert waves(report, "rocky") == [0, 1, 2, 3]


async def test_rocky_unit_drift_reloads_and_stops():
    """A loaded, stale, running unit: both halves of the upstream task fire.

    The offline fresh case only carries the seam's permissive marking (D8);
    here the seed supplies the real ``FragmentPath``/pending pair and the unit
    is active, so the one request reports the reload *and* the stop -- and the
    configure wave starts the unit again, upstream order preserved.
    """
    inventory = make_rocky_inventory()
    hosts = make_hosts(inventory, preinstalled=True, unit_drift=True)
    host = hosts["rocky"]
    report = await run_role(inventory, hosts)

    entry = entry_named(report, RELOAD_NAME, "rocky")
    assert entry.changed
    assert entry.reason == "daemon-reload; clamd-freshclam.service running, will stop"
    assert "$ sudo systemctl stop clamd-freshclam.service" in report.tree()
    assert host.remote_files[FRESHCLAM_UNIT_PATH] == FRESHCLAM_UNIT.encode()

    # Stopped by the reload task, then started by the service task below it.
    assert host.services["clamd-freshclam.service"] == "active"
    assert entry_named(report, FRESHCLAM_SERVICE_NAME, "rocky").reason == (
        "clamd-freshclam.service not running (inactive), will start; "
        "clamd-freshclam.service already enabled"
    )

    # A drifted unit converges into the ordinary converged stream.
    assert (await run_role(inventory, hosts)).tree() == RH_CONVERGED_TREE


async def test_rocky_dry_preview_stops_at_the_install_checkpoint():
    """The one-wave preview is the same on RedHat: the unit wave is never built."""
    inventory = make_rocky_inventory()
    hosts = make_hosts(inventory)
    host = hosts["rocky"]

    report = await run_role(inventory, hosts, dry=True)

    assert report.merged().log == []
    assert host.packages == {}
    assert host.ran_shells == []
    assert FRESHCLAM_UNIT_PATH not in host.remote_files
    assert scan_text(host) == SCAN_SEED
    assert report.tree() == RH_DRY_TREE


async def test_rocky_freshclam_edits_use_the_redhat_path():
    """The optional ``freshclam.conf`` loop follows the family table too (D7).

    Undefined by default, so the base RedHat path never touches it; set, the
    edit lands on ``/etc/freshclam.conf`` -- and still does not notify, and
    still does not reload.
    """
    proxy_regexp = r"^.*HTTPProxyServer .*$"
    inventory = make_rocky_inventory(
        freshclam_configuration_changes=[
            {"regexp": proxy_regexp, "line": "HTTPProxyServer proxy.example"}
        ]
    )
    hosts = make_hosts(inventory, preinstalled=True, clamd_content=SCAN_CONVERGED)
    host = hosts["rocky"]
    # Everything else already converged, so the freshclam edit is the only change.
    unit = FRESHCLAM_UNIT.encode()
    host.remote_files[FRESHCLAM_UNIT_PATH] = unit
    host.existing_files.add(FRESHCLAM_UNIT_PATH)
    host.file_stats[FRESHCLAM_UNIT_PATH] = {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(unit),
    }
    host.services = {"clamd@scan.service": "active", "clamd-freshclam.service": "active"}
    host.enabled_services = {"clamd@scan.service", "clamd-freshclam.service"}
    stale = b"# HTTPProxyServer scanner.example\n"
    host.remote_files[REDHAT_FRESHCLAM_CONF] = stale
    host.existing_files.add(REDHAT_FRESHCLAM_CONF)
    host.file_stats[REDHAT_FRESHCLAM_CONF] = {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(stale),
    }
    report = await run_role(inventory, hosts)

    assert host.remote_files[REDHAT_FRESHCLAM_CONF] == b"HTTPProxyServer proxy.example\n"
    assert FRESHCLAM_CONF not in host.remote_files
    assert [e.name for e in changed_entries(report)] == [
        freshclam_change_name(proxy_regexp)
    ]
    assert not has_entry(report, RELOAD_NAME, "rocky")
    assert not has_entry(report, RH_HANDLER_NAME, "rocky")
