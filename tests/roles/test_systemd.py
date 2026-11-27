# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``systemd`` role (§17.5).

The role is library code now, so it is imported (``from reemote.roles import
systemd``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, systemd(v), host_factory=...)``. The factory is plain
(non-``async``) -- every input is a resolved variable or a frozen constant -- so
it is called, not awaited.

Two assertions carry the migration's claims.

* **the stream is two waves, one more per user-scoped configuration** -- the
  rendered files, ``checkpoint("configure done")`` closing that wave, then the
  two handlers and the activations in the play's own order. A unit naming a
  non-root user prepends the ``fact(Users)`` wave, which is the role's single
  ``getent passwd`` read; a root-owned configuration costs no wave for it (D2),
  so the two hosts of one inventory can sit in different wave counts and both
  converge.
* **the notification is per request, not per host** (D3) -- the reload handler
  loops over the registered render results and gates on each item's own
  ``changed``, so ``changed_names`` reads the checkpoint's log by name. The
  tests prove the asymmetry: a rerun that changes one unit file reloads exactly
  that unit, and a converged pass carries no handler record at all.

**No between-pass bridge is needed**: the simulation's own mutators maintain
every seam the second pass reads. ``install_file`` records the rendered content,
its stat row and the parent directory, and marks the systemd cache stale only
for a path under ``/systemd/system``; ``make_directory`` the load-path rows; the
``systemctl`` arms the running/enabled state and clear the stale-cache flag on a
``daemon-reload`` -- which is why ``daemon_reload=True`` on the unit handler is a
no-op on the rerun instead of churn (issue #50).

Seeding choices, none of them a bridge:

* ``os_users`` is the ``Users`` fact's seam, so the passwd read the per-user
  branch takes is the seeded dictionary. Both accounts the tests name are in it:
  the owner-existence assert loops over *every* entry, so a configuration
  holding ``sduser`` still needs ``root`` present.
* ``/etc/systemd/system`` is seeded as existing at ``0755``, because every
  Ubuntu ships it. No request of the role's manages it -- a unit that names no
  ``path`` only names it as its destination -- so the row exists to be written
  into, not to be corrected.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from reemote import run_waves
from reemote.modules.server import Users
from reemote.roles import SystemdVars, systemd
from reemote.roles.systemd import (
    DEFAULT_UNIT_PATH,
    DEFAULT_UNIT_STATE,
    PID1_UNIT_PATH_PREFIXES,
    UnitPlan,
    comment_filter,
    manager_args,
    needs_passwd_read,
    render_dropin_file,
    render_unit_file,
    resolve_units,
    unit_owner,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from reemote.stages import CheckpointRequest, FactRequest, scoped
from tests.simulated_host import SimulatedHost

#: The three units of the default configuration: a service that names a state,
#: a socket that names none, and the drop-in that overrides the service.
SERVICE_DEST = f"{DEFAULT_UNIT_PATH}/reemote-service.service"
SOCKET_DEST = f"{DEFAULT_UNIT_PATH}/reemote-socket.socket"
DROPIN_DIR = f"{SERVICE_DEST}.d"
DROPIN_DEST = f"{DROPIN_DIR}/reemote-override.conf"
USER_DIR = "/home/sduser/.config/systemd/user"
USER_DEST = f"{USER_DIR}/reemote-job.service"

SERVICE_ENTRY = {
    "name": "reemote-service",
    "state": "started",
    "enabled": True,
    "Unit": {"Description": "A managed service"},
    "Service": {"ExecStart": "/usr/bin/sleep infinity"},
    "Install": {"WantedBy": "multi-user.target"},
}
SOCKET_ENTRY = {
    "name": "reemote-socket",
    "type": "socket",
    "enabled": True,
    "Socket": {"ListenStream": "8080"},
    "Install": {"WantedBy": "sockets.target"},
}
DROPIN_ENTRY = {
    "name": "reemote-override",
    "type": "conf",
    "path": DROPIN_DIR,
    "Service": {"Environment": ["REEMOTE=1"]},
}
USER_ENTRY = {
    "name": "reemote-job",
    "path": USER_DIR,
    "user": "sduser",
    "state": "started",
    "enabled": True,
    "Service": {"ExecStart": "/usr/bin/sleep 100"},
    "Install": {"WantedBy": "default.target"},
}

UNITS = (SERVICE_ENTRY, SOCKET_ENTRY, DROPIN_ENTRY)

#: ``configure``'s three names in loop order. The drop-in's directory task sits
#: between the two system units because it belongs to the third entry.
CONFIGURE_NAMES = (
    f"Render systemd unit file {SERVICE_DEST}",
    f"Render systemd unit file {SOCKET_DEST}",
    f"Create unit configuration path ({DROPIN_DIR})",
    f"Render systemd drop-in file {DROPIN_DEST}",
)
#: The two handlers' names for the default configuration: both units reload (the
#: socket's entry names no state, so its reload leaves the active state alone),
#: and the changed drop-in reloads the manager.
HANDLER_NAMES = (
    "Reload systemd unit reemote-service.service",
    "Reload systemd unit reemote-socket.socket",
    f"Reload systemd daemon ({DROPIN_DEST})",
)
LAUNCH_NAMES = (
    "Activate systemd unit reemote-service.service",
    "Activate systemd unit reemote-socket.socket",
)


def _user_row(name: str, uid: int, **fields: Any) -> dict:
    """A complete ``os_users`` row for seeding (every ``Users`` fact key)."""
    row = {
        "home": "/root" if name == "root" else f"/home/{name}",
        "comment": None,
        "shell": "/bin/bash",
        "group": name,
        "groups": [],
        "uid": uid,
        "gid": uid,
        "lastlog": None,
        "login_time": None,
        "password": "",
    }
    row.update(fields)
    return row


def _role_vars(overrides: dict[str, Any]) -> dict[str, Any]:
    """The base ``roles.systemd`` mapping, overlaid with *overrides*."""
    mapping: dict[str, Any] = {"unit_config": [dict(entry) for entry in UNITS]}
    mapping.update(overrides)
    return mapping


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host to be skipped.

    The role variables ride on the entry's ``roles`` mapping under
    ``"systemd"``, written with the model's bare field names, which is what
    ``resolve_vars(SystemdVars, "systemd", inventory)`` binds. The *same* mapping
    is given to the RedHat host too, so a silent off-target host proves the
    family guard rather than an absent configuration.
    """
    return [
        {
            "host": "ubuntu",
            "family": "Ubuntu",
            "connection": {
                "address": "192.0.2.10",
                "username": "supervisor",
                "key_path": "~/.ssh/id_ed25519",
            },
            "group": "ubuntu",
            "roles": {"systemd": _role_vars(overrides)},
        },
        {
            "host": "web1",
            "family": "RedHat",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "centos",
            "roles": {"systemd": _role_vars(overrides)},
        },
    ]


def seed(host: SimulatedHost) -> None:
    """Pre-state: a stock Ubuntu machine carrying none of the managed units.

    See the module docstring for why nothing here is a between-pass bridge.
    """
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}
    host.directory_stats = {
        DEFAULT_UNIT_PATH: {"mode": 755, "user": "root", "group": "root"},
    }
    host.links = {}
    host.link_stats = {}
    host.services = {}
    host.enabled_services = set()
    host.os_users = {
        "root": _user_row("root", 0),
        "sduser": _user_row("sduser", 1234),
    }


def make_hosts(inventory: list[dict[str, Any]]) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded target.

    Only the Ubuntu entries are seeded, so an accidental request on the RedHat
    host is caught by the untouched-store assertions.
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
    v = resolve_vars(SystemdVars, "systemd", inventory)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    report, _hosts = await run_waves(
        inventory, systemd(v), host_factory=factory, dry=dry
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


def names(report: Any, host: str = "ubuntu") -> list[str]:
    """*host*'s log entry names of one pass, in order."""
    return [entry.name for entry in report.merged().log if entry.host == host]


def host_records(report: Any, host: str) -> list[Any]:
    """Every record (request step or barrier) attributed to *host*."""
    return [record for record in report.records if record.host == host]


def barriers(report: Any, host: str = "ubuntu") -> list[Any]:
    """The barrier objects *host* parked on, in wave order."""
    return [record.barrier for record in host_records(report, host) if record.barrier]


def waves(report: Any, host: str = "ubuntu") -> list[int]:
    """The distinct wave numbers *host* emitted records in."""
    return sorted({record.wave for record in host_records(report, host)})


def request_kwargs(report: Any, name: str, host: str = "ubuntu") -> dict:
    """The forwarded keyword arguments of the one planned request called *name*."""
    record = next(
        record
        for record in report.records
        if record.host == host
        and record.planned is not None
        and record.planned.request.name == name
    )
    return record.planned.request.kwargs


def planned_wave(report: Any, name: str, host: str = "ubuntu") -> int:
    """The wave number that carried the one planned request called *name*."""
    return next(
        record.wave
        for record in report.records
        if record.host == host
        and record.planned is not None
        and record.planned.request.name == name
    )


def request_env(report: Any, name: str, host: str = "ubuntu") -> tuple:
    """The normalised ``env`` meta-parameter of the one planned request.

    ``op()`` lifts ``env`` out of the operation's keyword arguments onto the
    request itself and sorts it into a tuple of pairs, so the ``XDG_RUNTIME_DIR``
    assertion reads ``request.env``, not ``request.kwargs``.
    """
    record = next(
        record
        for record in report.records
        if record.host == host
        and record.planned is not None
        and record.planned.request.name == name
    )
    return record.planned.request.env


def text(host: SimulatedHost, path: str) -> str:
    """What the role rendered into the remote file at *path*."""
    return host.remote_files[path].decode("utf-8")


# --- the stream shape ---------------------------------------------------------


async def test_stream_is_two_waves_for_root_owned_units():
    """Configure, checkpoint, handlers, launch: two waves, one barrier.

    No passwd read sits before a root-owned system unit, so the whole task list
    is wave 0's requests plus ``checkpoint("configure done")`` and wave 1's
    handlers and activations -- the role's ``notify`` pair and its
    ``meta: flush_handlers`` folded into one stream.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert [type(b).__name__ for b in barriers(report)] == ["CheckpointRequest"]
    assert isinstance(barriers(report)[0], CheckpointRequest)
    assert barriers(report)[0].name == "configure done"
    assert waves(report) == [0, 1]
    assert names(report) == [*CONFIGURE_NAMES, *HANDLER_NAMES, *LAUNCH_NAMES]


async def test_a_user_unit_adds_the_passwd_barrier():
    """Wave 0 is the role's ``getent passwd``, and only a per-user unit takes it.

    The read feeds all three derived values -- the owner-existence assert, the
    home behind ``_own_as_user``, and the uid behind ``XDG_RUNTIME_DIR`` -- so
    one barrier covers what the role's vars blocks asked of ``getent_passwd``
    per loop item (D2).
    """
    inventory = make_inventory(unit_config=[*UNITS, USER_ENTRY])
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert [type(b).__name__ for b in barriers(report)] == [
        "FactRequest",
        "CheckpointRequest",
    ]
    assert isinstance(barriers(report)[0], FactRequest)
    assert barriers(report)[0].fact_cls is Users
    assert waves(report) == [0, 1, 2]


async def test_the_handlers_follow_the_launch_in_one_wave():
    """``flush_handlers`` becomes ordering inside the stream, not another wave.

    The driver applies a wave in order and pulls the next request after each, so
    the manager reload precedes the ``systemctl start`` that needs the reloaded
    unit file -- the guarantee the role bought by flushing its handlers, with the
    requests still landing in the same wave.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    reload_wave = planned_wave(report, HANDLER_NAMES[0])
    daemon_wave = planned_wave(report, HANDLER_NAMES[2])
    launch_wave = planned_wave(report, LAUNCH_NAMES[0])
    assert reload_wave == daemon_wave == launch_wave == 1
    ordered = names(report)
    assert ordered.index(HANDLER_NAMES[0]) < ordered.index(LAUNCH_NAMES[0])
    assert ordered.index(HANDLER_NAMES[2]) < ordered.index(LAUNCH_NAMES[0])


# --- the first pass really deploys the role -----------------------------------


async def test_first_pass_renders_activates_and_enables():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # The unit file is the entry's sections, in the template's order, under the
    # role's ``ansible_managed`` header comment.
    assert text(host, SERVICE_DEST) == (
        "# Ansible managed\n"
        "# see: Systemd Unit configuration logic"
        " -- http://man7.org/linux/man-pages/man5/systemd.unit.5.html"
        " for more details\n"
        "\n"
        "[Unit]\n"
        "Description=A managed service\n"
        "\n"
        "[Service]\n"
        "ExecStart=/usr/bin/sleep infinity\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    assert text(host, SOCKET_DEST) == (
        "# Ansible managed\n"
        "# see: Systemd Unit configuration logic"
        " -- http://man7.org/linux/man-pages/man5/systemd.unit.5.html"
        " for more details\n"
        "\n"
        "\n"
        "[Socket]\n"
        "ListenStream=8080\n"
        "\n"
        "[Install]\n"
        "WantedBy=sockets.target\n"
    )
    assert text(host, DROPIN_DEST) == (
        "# Ansible managed\n"
        "# see: Systemd Unit configuration logic"
        " -- http://man7.org/linux/man-pages/man5/systemd.unit.5.html"
        " for more details\n"
        "\n"
        "[Service]\n"
        "Environment=REEMOTE=1\n"
    )

    # Every file: ``mode: "0644"``, owned by root (``_own_as_user`` is false for
    # a system load path whatever the entry's user).
    for path in (SERVICE_DEST, SOCKET_DEST, DROPIN_DEST):
        assert host.file_stats[path] == {
            "user": "root",
            "group": "root",
            "mode": 644,
            "size": len(host.remote_files[path]),
        }

    # The drop-in's directory is the one task the entry's ``path`` gates.
    assert host.directory_stats[DROPIN_DIR] == {
        "mode": 755,
        "user": "root",
        "group": "root",
    }

    # State and boot setting, per entry.
    assert host.services["reemote-service.service"] == "active"
    assert "reemote-socket.socket" not in host.services
    assert host.enabled_services == {"reemote-service.service", "reemote-socket.socket"}

    assert [entry.name for entry in changed_entries(report)] == [
        *CONFIGURE_NAMES,
        "Reload systemd unit reemote-service.service",
        f"Reload systemd daemon ({DROPIN_DEST})",
        *LAUNCH_NAMES,
    ]


async def test_the_socket_entry_takes_its_own_section():
    """``config.type|default('service')|title`` is both the filename and the section."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)
    content = text(hosts["ubuntu"], SOCKET_DEST)

    assert "[Socket]" in content
    assert "[Service]" not in content
    assert "[Unit]" not in content


async def test_the_drop_in_directory_is_only_for_an_entry_that_names_a_path():
    """``when: unit_item.path is defined`` -- the two system units name none."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    directories = [name for name in names(report) if name.startswith("Create unit")]
    assert directories == [f"Create unit configuration path ({DROPIN_DIR})"]
    # ``path`` is still resolved for an entry that omits it -- as the destination
    # -- but resolving it creates nothing.
    assert hosts["ubuntu"].directory_stats[DEFAULT_UNIT_PATH]["mode"] == 755


# --- the handlers -------------------------------------------------------------


async def test_only_a_changed_unit_queues_its_own_reload():
    """D3: the handler loops over registered results, gating on each ``changed``.

    Converge the host, then change one unit file's content. Only that unit's
    render task reports changed, so only that unit is reloaded: the other unit's
    reload and the drop-in's daemon reload are absent from the pass entirely --
    not skipped, never planned.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    changed_service = {**SERVICE_ENTRY, "Unit": {"Description": "Now something else"}}
    second_inventory = make_inventory(unit_config=[changed_service, SOCKET_ENTRY, DROPIN_ENTRY])
    second = await run_role(second_inventory, hosts)

    assert names(second) == [
        *CONFIGURE_NAMES,
        "Reload systemd unit reemote-service.service",
        *LAUNCH_NAMES,
    ]
    assert entry_named(second, f"Render systemd unit file {SERVICE_DEST}").changed
    assert not entry_named(second, f"Render systemd drop-in file {DROPIN_DEST}").changed
    assert f"Reload systemd daemon ({DROPIN_DEST})" not in names(second)


async def test_a_drop_in_queues_the_daemon_reload_and_not_the_unit_reload():
    """Two handlers, two notification channels: ``render_units`` vs ``render_dropins``.

    The drop-in is never a unit -- the role's handler list for unit reloads comes
    from the unit-render task only -- so a changed ``.conf`` reloads the manager
    and nothing else.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    changed_dropin = {**DROPIN_ENTRY, "Service": {"Environment": ["REEMOTE=2"]}}
    second_inventory = make_inventory(unit_config=[SERVICE_ENTRY, SOCKET_ENTRY, changed_dropin])
    second = await run_role(second_inventory, hosts)

    assert names(second) == [
        *CONFIGURE_NAMES,
        f"Reload systemd daemon ({DROPIN_DEST})",
        *LAUNCH_NAMES,
    ]
    assert "Reload systemd unit reemote-override.conf" not in names(second)


async def test_reload_on_change_false_keeps_the_unit_out_of_the_handler():
    """``reload_on_change is not defined or reload_on_change`` -- the second gate."""
    inventory = make_inventory(unit_config=[{**SERVICE_ENTRY, "reload_on_change": False}])
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert names(report) == [
        f"Render systemd unit file {SERVICE_DEST}",
        "Activate systemd unit reemote-service.service",
    ]
    assert entry_named(report, f"Render systemd unit file {SERVICE_DEST}").changed

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_a_template_unit_is_rendered_but_never_activated():
    """``name is not search('^[^@]+@$')`` gates both handlers and the launch task."""
    template = {
        "name": "reemote-getty@",
        "state": "started",
        "Service": {"ExecStart": "/sbin/agetty %I"},
    }
    inventory = make_inventory(unit_config=[template])
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert names(report) == [f"Render systemd unit file {DEFAULT_UNIT_PATH}/reemote-getty@.service"]
    assert hosts["ubuntu"].remote_files[f"{DEFAULT_UNIT_PATH}/reemote-getty@.service"]
    assert hosts["ubuntu"].services == {}

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the launch task ----------------------------------------------------------


async def test_an_entry_naming_no_state_is_stopped_at_launch_and_omitted_by_the_handler():
    """The two ``default(omit)`` arms of the role, kept apart by :attr:`UnitPlan.state`.

    ``launch`` falls back to ``_default_unit_state`` (``stopped``), while the
    reload handler passes no state at all -- so a unit that names nothing is
    left alone by the reload and then *stopped* by the activation, which is what
    the role's two differently-spelled defaults do.
    """
    assert DEFAULT_UNIT_STATE == "stopped"
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    reload_kwargs = request_kwargs(report, "Reload systemd unit reemote-socket.socket")
    assert reload_kwargs["running"] is None
    launch_kwargs = request_kwargs(report, "Activate systemd unit reemote-socket.socket")
    assert launch_kwargs["running"] is False
    assert launch_kwargs["enabled"] is True
    # The service named ``state: started``, so its reload starts it too.
    assert request_kwargs(report, HANDLER_NAMES[0])["running"] is True


async def test_manage_state_false_leaves_the_running_state_alone():
    """``if manage_state else omit`` -- boot setting without touching the unit.

    An entry that names neither a state nor a reload leaves the unit exactly as
    it found it and only sets ``enabled``, which is the whole point of the role's
    ``manage_state`` knob.
    """
    inventory = make_inventory(
        unit_config=[
            {
                "name": "reemote-service",
                "manage_state": False,
                "enabled": True,
                "Unit": {"Description": "A managed service"},
                "Service": {"ExecStart": "/usr/bin/sleep infinity"},
                "Install": {"WantedBy": "multi-user.target"},
            }
        ]
    )
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    launch_kwargs = request_kwargs(report, "Activate systemd unit reemote-service.service")
    assert launch_kwargs["running"] is None
    assert launch_kwargs["enabled"] is True
    assert request_kwargs(report, HANDLER_NAMES[0])["running"] is None
    # Nothing asked for the unit to be started, so it never was.
    assert hosts["ubuntu"].services == {}
    assert hosts["ubuntu"].enabled_services == {"reemote-service.service"}

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_the_reload_handler_ignores_manage_state_as_the_role_does():
    """The role's asymmetry, kept: only ``launch`` reads ``manage_state``.

    The handler's ``state: {{ item.unit_item.state | default(omit) }}`` has no
    ``manage_state`` arm, so a unit that both names ``state: started`` and turns
    state management off is still *started* by its own reload -- and then left
    alone by the activation.
    """
    inventory = make_inventory(unit_config=[{**SERVICE_ENTRY, "manage_state": False}])
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert request_kwargs(report, HANDLER_NAMES[0])["running"] is True
    assert request_kwargs(report, "Activate systemd unit reemote-service.service")[
        "running"
    ] is None
    assert hosts["ubuntu"].services == {"reemote-service.service": "active"}


async def test_enabled_defaults_to_taking_the_unit_off_boot():
    """``enabled|default('no')`` is passed unconditionally, so absence is enforced."""
    inventory = make_inventory(unit_config=[{"name": "quiet", "Service": {"ExecStart": "/true"}}])
    hosts = make_hosts(inventory)
    hosts["ubuntu"].enabled_services = {"quiet.service"}
    hosts["ubuntu"].services = {"quiet.service": "active"}
    report = await run_role(inventory, hosts)

    kwargs = request_kwargs(report, "Activate systemd unit quiet.service")
    assert kwargs["enabled"] is False
    assert kwargs["running"] is False
    assert hosts["ubuntu"].enabled_services == set()
    assert hosts["ubuntu"].services["quiet.service"] == "inactive"

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- per-user units -----------------------------------------------------------


async def test_a_user_unit_is_owned_by_that_user_and_addressed_to_its_manager():
    """D1: ``become_user`` becomes file ownership plus a named manager.

    The unit under ``/home/sduser`` is installed ``sduser:sduser`` by the
    root-owned spool-and-install, and every ``systemctl`` for it runs as root
    against ``--machine=sduser@.host`` with the role's ``XDG_RUNTIME_DIR``
    environment, which is the same manager ``sudo -u sduser systemctl --user``
    talked to.
    """
    inventory = make_inventory(unit_config=[USER_ENTRY])
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    assert host.file_stats[USER_DEST] == {
        "user": "sduser",
        "group": "sduser",
        "mode": 644,
        "size": len(host.remote_files[USER_DEST]),
    }
    assert host.directory_stats[USER_DIR] == {
        "mode": 755,
        "user": "sduser",
        "group": "sduser",
    }

    for name in (
        "Reload systemd unit reemote-job.service",
        "Activate systemd unit reemote-job.service",
    ):
        assert request_kwargs(report, name)["user_name"] == "sduser"
        assert request_env(report, name) == (("XDG_RUNTIME_DIR", "/run/user/1234"),)
    assert host.services["reemote-job.service"] == "active"
    assert host.enabled_services == {"reemote-job.service"}

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_a_system_unit_for_a_non_root_user_stays_root_owned():
    """``_own_as_user`` needs *both* the user scope and a home path.

    A unit written into ``/etc`` for another user is still a system unit: the
    role chowns it to root and reloads the *system* manager, because that is the
    manager that reads the path.
    """
    inventory = make_inventory(
        unit_config=[{**SERVICE_ENTRY, "user": "sduser", "scope": "system"}]
    )
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert hosts["ubuntu"].file_stats[SERVICE_DEST]["user"] == "root"
    kwargs = request_kwargs(report, "Activate systemd unit reemote-service.service")
    assert "user_name" not in kwargs
    assert "env" not in kwargs


async def test_a_group_of_its_own_is_used_for_the_file():
    inventory = make_inventory(
        unit_config=[
            {**USER_ENTRY, "group": "staff"},
        ]
    )
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    assert hosts["ubuntu"].file_stats[USER_DEST]["group"] == "staff"
    assert hosts["ubuntu"].file_stats[USER_DEST]["user"] == "sduser"


# --- the preflight ------------------------------------------------------------


async def test_a_user_scope_unit_on_a_system_path_is_refused():
    """The role's third preflight, raised before a single request is built."""
    inventory = make_inventory(unit_config=[{**USER_ENTRY, "path": DEFAULT_UNIT_PATH}])
    hosts = make_hosts(inventory)

    with pytest.raises(ValueError, match="cannot use system unit path"):
        await run_role(inventory, hosts)

    assert hosts["ubuntu"].remote_files == {}
    assert hosts["ubuntu"].services == {}


async def test_a_user_scope_unit_with_no_path_is_refused():
    """No ``path`` resolves to ``/etc/systemd/system``, which is a PID 1 path."""
    inventory = make_inventory(
        unit_config=[{"name": "orphan", "user": "sduser", "Service": {}}]
    )
    hosts = make_hosts(inventory)

    with pytest.raises(ValueError, match="orphan.service"):
        await run_role(inventory, hosts)


async def test_a_missing_owner_is_refused_after_the_passwd_read():
    """D8: the two asserts keep their order -- path first (pure), passwd second."""
    inventory = make_inventory(
        unit_config=[
            {
                "name": "ghost",
                "user": "nobody",
                "path": "/home/nobody/.config/systemd/user",
            },
        ]
    )
    hosts = make_hosts(inventory)

    with pytest.raises(ValueError, match="was not found in passwd"):
        await run_role(inventory, hosts)

    # The barrier ran, the configuration did not.
    assert hosts["ubuntu"].remote_files == {}
    assert hosts["ubuntu"].directory_stats == {
        DEFAULT_UNIT_PATH: {"mode": 755, "user": "root", "group": "root"}
    }


# --- the guards ---------------------------------------------------------------


async def test_non_ubuntu_host_emits_nothing():
    """The family guard is the body's opening ``if``, not a ``when`` on each op."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    skipped = hosts["web1"]
    assert host_records(report, "web1") == []
    assert [e for e in report.merged().log if e.host == "web1"] == []
    assert skipped.remote_files == {}
    assert skipped.services == {}
    assert skipped.enabled_services == set()


async def test_an_empty_unit_config_emits_nothing():
    """Every preflight task of the role is gated ``when: unit_config | length > 0``."""
    inventory = make_inventory(unit_config=[])
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert host_records(report, "ubuntu") == []
    assert changed_entries(report) == []


async def test_two_hosts_converge_from_different_wave_counts():
    """One host takes the passwd barrier, the other does not, and both converge.

    A wave with no records is not appended, so the Ubuntu host with a per-user
    unit runs three waves while its neighbour runs two -- and the driver still
    advances them in lockstep, which is what the barrier model bought.
    """
    first = make_inventory(unit_config=[*UNITS, USER_ENTRY])[0]
    second = {
        **first,
        "host": "ubuntu2",
        "connection": {**first["connection"], "address": "192.0.2.12"},
        "roles": {"systemd": {"unit_config": [dict(entry) for entry in UNITS]}},
    }
    inventory = [first, second]
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert waves(report, "ubuntu") == [0, 1, 2]
    assert waves(report, "ubuntu2") == [0, 1]
    assert [type(b).__name__ for b in barriers(report, "ubuntu2")] == [
        "CheckpointRequest"
    ]

    converged = await run_role(inventory, hosts)
    assert changed_entries(converged) == []
    assert waves(converged, "ubuntu") == [0, 1, 2]


# --- convergence and the preview ----------------------------------------------


async def test_second_pass_changes_nothing():
    """The rerun is the honest preview: no change, no reload, no activation work.

    Content-equal installs report nothing, so the checkpoint notifies neither
    handler; ``daemon_reload`` is state-gated and the flag the first pass set was
    cleared by the reload that consumed it (issue #50); and the launch task's
    defaults are already satisfied.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert names(second) == [*CONFIGURE_NAMES, *LAUNCH_NAMES]
    for name in HANDLER_NAMES:
        assert name not in names(second)


async def test_dry_preview_stops_at_the_first_barrier():
    """A root-owned configuration plans its whole wave 0 and parks on the checkpoint.

    With no ``fact`` in front of it, the dry pass of the default configuration is
    complete for wave 0: every render and directory request is planned with its
    real pre-state, and the checkpoint is the honest
    ``unknown until run`` (§16.6).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts, dry=True)

    assert waves(report) == [0]
    assert [type(b).__name__ for b in barriers(report)] == ["CheckpointRequest"]
    assert "configure done → unknown until run" in report.tree()
    assert changed_entries(report) == []
    assert hosts["ubuntu"].remote_files == {}
    assert hosts["ubuntu"].services == {}


async def test_dry_preview_of_a_user_unit_stops_at_the_passwd_read():
    """The one case where the preview is *shallower*: wave 0 is the read itself."""
    inventory = make_inventory(unit_config=[USER_ENTRY])
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts, dry=True)

    assert [type(b).__name__ for b in barriers(report)] == ["FactRequest"]
    assert waves(report) == [0]
    assert hosts["ubuntu"].remote_files == {}


# --- uninstall mode -----------------------------------------------------------


async def test_uninstall_stops_disables_and_removes_without_writing():
    """``perform_uninstall`` replaces the config and launch stages with the handler.

    The role's ``command: /bin/true`` + ``changed_when: true`` signal task needs
    no counterpart -- the flag is a variable the body reads -- so the mode is
    one wave of removals and no checkpoint at all.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    uninstall_inventory = make_inventory(perform_uninstall=True)
    report = await run_role(uninstall_inventory, hosts)

    assert barriers(report) == []
    assert waves(report) == [0]
    assert names(report) == [
        "Stop and disable systemd unit reemote-service.service",
        "Stop and disable systemd unit reemote-socket.socket",
        f"Remove systemd unit file {SERVICE_DEST}",
        f"Remove systemd unit file {SOCKET_DEST}",
        f"Remove systemd unit file {DROPIN_DEST}",
    ]
    assert hosts["ubuntu"].remote_files == {}
    assert hosts["ubuntu"].enabled_services == set()
    assert hosts["ubuntu"].services["reemote-service.service"] == "inactive"
    # The drop-in's *directory* is not one of the managed files, as in the role.
    assert DROPIN_DIR in hosts["ubuntu"].directory_stats

    second = await run_role(uninstall_inventory, hosts)
    assert changed_entries(second) == []


async def test_uninstall_skips_units_a_handler_would_skip():
    """``when`` of the ``Uninstall units`` handler: no drop-ins, no template units.

    The *file* removal still covers every entry, which is the asymmetry the
    handler's two tasks have -- one loops over units, the other over all dests.
    """
    units = [
        USER_ENTRY,
        {"name": "reemote-getty@", "Service": {"ExecStart": "/sbin/agetty %I"}},
        DROPIN_ENTRY,
    ]
    inventory = make_inventory(unit_config=units)
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    report = await run_role(make_inventory(unit_config=units, perform_uninstall=True), hosts)
    stoppers = [name for name in names(report) if name.startswith("Stop and disable")]
    removals = [name for name in names(report) if name.startswith("Remove systemd")]

    assert stoppers == ["Stop and disable systemd unit reemote-job.service"]
    assert len(removals) == 3


async def test_uninstall_reaches_the_user_manager_for_a_per_user_unit():
    inventory = make_inventory(unit_config=[USER_ENTRY])
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    report = await run_role(
        make_inventory(unit_config=[USER_ENTRY], perform_uninstall=True), hosts
    )
    kwargs = request_kwargs(report, "Stop and disable systemd unit reemote-job.service")
    assert kwargs["running"] is False
    assert kwargs["enabled"] is False
    assert kwargs["user_name"] == "sduser"
    assert request_env(report, "Stop and disable systemd unit reemote-job.service") == (
        ("XDG_RUNTIME_DIR", "/run/user/1234"),
    )


# --- the variables ------------------------------------------------------------


def test_vars_reject_a_global_scope():
    """D7: reemote's systemd descriptors never reach ``systemctl --global``."""
    with pytest.raises(ValidationError, match="scope"):
        SystemdVars(unit_config=({"name": "x", "scope": "global"},))


def test_vars_reject_an_unknown_unit_type():
    with pytest.raises(ValidationError, match="type"):
        SystemdVars(unit_config=({"name": "x", "type": "banana"},))


def test_vars_reject_a_nameless_entry():
    with pytest.raises(ValidationError, match="needs a name"):
        SystemdVars(unit_config=({"name": ""},))


def test_vars_reject_an_unknown_variable():
    """A misspelled inventory key fails as ``RoleVarsError``, not a silent drop."""
    inventory = make_inventory()
    inventory[0]["roles"]["systemd"]["unit_confg"] = [dict(SERVICE_ENTRY)]
    with pytest.raises(RoleVarsError, match="unit_confg"):
        resolve_vars(SystemdVars, "systemd", inventory)


async def test_inert_variables_still_bind():
    """``systemd_backup_files`` is carried and does nothing (D5)."""
    inventory = make_inventory(backup_files=False)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert resolve_vars(SystemdVars, "systemd", inventory)["ubuntu"].backup_files is False
    assert names(report) == [*CONFIGURE_NAMES, *HANDLER_NAMES, *LAUNCH_NAMES]


async def test_host_overrides_are_bound_per_host():
    """The inventory mapping is per host, so each unit list is that host's own."""
    inventory = make_inventory()
    inventory[1]["family"] = "Ubuntu"
    inventory[1]["host"] = "ubuntu2"
    inventory[1]["roles"] = {"systemd": {"unit_config": [{"name": "only", "Service": {}}]}}
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    only_dest = f"{DEFAULT_UNIT_PATH}/only.service"
    assert entry_named(report, f"Render systemd unit file {only_dest}", "ubuntu2").changed
    assert list(hosts["ubuntu2"].remote_files) == [only_dest]
    assert SERVICE_DEST in hosts["ubuntu"].remote_files


# --- the pure half, unit-tested directly --------------------------------------


def test_comment_filter_matches_ansible():
    assert comment_filter("Ansible managed") == "# Ansible managed"
    assert comment_filter("one\ntwo", "# ") == "# one\n# two"
    assert comment_filter("") == ""


def test_render_unit_file_follows_the_sections_present():
    config = {"name": "x", "type": "service", "Unit": {"Description": "d"}}
    rendered = render_unit_file(config)

    assert rendered.startswith("# Ansible managed\n")
    assert "[Unit]\nDescription=d\n" in rendered
    # No ``Service`` key means no ``[Service]`` section, and no bare ``[x]``.
    assert "[Service]" not in rendered
    assert "[Install]" not in rendered


def test_render_unit_file_expands_a_list_value():
    """A list value repeats the key, which is how ``Environment=`` takes several."""
    rendered = render_unit_file(
        {"name": "x", "Service": {"Environment": ["A=1", "B=2"], "ExecStart": "/bin/true"}}
    )
    assert "Environment=A=1\nEnvironment=B=2\n" in rendered
    assert "ExecStart=/bin/true\n" in rendered


def test_render_dropin_file_only_takes_title_cased_keys():
    rendered = render_dropin_file(
        {
            "name": "x",
            "type": "conf",
            "path": "/etc/systemd/system/x.service.d",
            "Service": {"ExecStart": ["", "-/bin/true"]},
        }
    )
    assert "[Service]" in rendered
    # The entry's own keys are not sections; ``ExecStart`` as a list repeats,
    # including the empty item the role's override example relies on.
    assert "name=" not in rendered
    assert "ExecStart=\nExecStart=-/bin/true\n" in rendered


def test_resolve_units_applies_the_roles_defaults():
    v = SystemdVars(unit_config=({"name": "plain", "user": "svc"},))
    (plan,) = resolve_units(v)

    assert isinstance(plan, UnitPlan)
    assert plan.unit_type == "service"
    assert plan.filename == "plain.service"
    assert plan.path == DEFAULT_UNIT_PATH
    assert plan.path_defined is False
    assert plan.dest == f"{DEFAULT_UNIT_PATH}/plain.service"
    assert plan.user == "svc"
    assert plan.group == "svc"
    assert plan.scope == "user"
    assert plan.state is None
    assert plan.manage_state is True
    assert plan.enabled is False
    assert plan.reload_on_change is True
    assert plan.is_conf is False
    assert plan.is_instance is False
    assert plan.render_name == f"Render systemd unit file {DEFAULT_UNIT_PATH}/plain.service"
    assert hash(plan)

    (conf,) = resolve_units(
        SystemdVars(unit_config=({"name": "d", "type": "conf", "path": "/etc/systemd/system/x.service.d"},))
    )
    assert conf.is_conf is True
    assert conf.render_name == f"Render systemd drop-in file {conf.dest}"


def test_resolve_units_flags_a_template_instance():
    (plan,) = resolve_units(SystemdVars(unit_config=({"name": "getty@"},)))
    assert plan.is_instance is True
    assert plan.filename == "getty@.service"


def test_passwd_read_is_only_needed_for_a_non_root_owner_or_scope():
    root_only = resolve_units(SystemdVars(unit_config=({"name": "a"},)))
    assert needs_passwd_read(root_only) is False

    by_user = resolve_units(SystemdVars(unit_config=({"name": "a", "user": "svc"},)))
    assert needs_passwd_read(by_user) is True

    by_scope = resolve_units(
        SystemdVars(unit_config=({"name": "a", "scope": "user", "path": "/home/svc/x"},))
    )
    assert needs_passwd_read(by_scope) is True


def test_unit_owner_requires_the_home_to_match():
    users = {"svc": _user_row("svc", 1234)}
    in_home = resolve_units(
        SystemdVars(unit_config=({"name": "a", "user": "svc", "path": "/home/svc/.config/systemd/user"},))
    )[0]
    assert unit_owner(in_home, users) == ("svc", "svc")

    # A system path is root's, whatever the entry's user.
    elsewhere = resolve_units(
        SystemdVars(unit_config=({"name": "a", "user": "svc", "path": "/etc/systemd/system"},))
    )[0]
    assert unit_owner(elsewhere, users) == ("root", "root")

    # ``_own_as_user`` compares against the *owner's* home: a unit under another
    # account's home stays root-owned, which is the role's symlink defence.
    wrong_home = resolve_units(
        SystemdVars(unit_config=({"name": "a", "user": "svc", "path": "/home/other/.config/systemd/user"},))
    )[0]
    assert unit_owner(wrong_home, users) == ("root", "root")

    # A user the passwd read does not know has no home to match (D2: the assert
    # that normally catches this runs first, so this arm is defence in depth).
    assert unit_owner(in_home, {}) == ("root", "root")
    assert unit_owner(in_home, None) == ("root", "root")


def test_manager_args_are_empty_for_a_system_unit():
    users = {"svc": _user_row("svc", 1234)}
    system = resolve_units(SystemdVars(unit_config=({"name": "a"},)))[0]
    assert manager_args(system, users) == {}

    user = resolve_units(
        SystemdVars(unit_config=({"name": "a", "user": "svc", "path": "/home/svc/.config/systemd/user"},))
    )[0]
    assert manager_args(user, users) == {
        "user_name": "svc",
        "env": (("XDG_RUNTIME_DIR", "/run/user/1234"),),
    }
    # The env value is a tuple of pairs, never a dict: the request must hash.
    assert isinstance(manager_args(user, users)["env"], tuple)
    # No uid in the read means no environment, not a bogus ``/run/user/None``.
    assert manager_args(user, {}) == {"user_name": "svc"}


def test_the_pid1_path_list_is_the_roles():
    assert DEFAULT_UNIT_PATH in PID1_UNIT_PATH_PREFIXES
    assert "/lib/systemd/system" in PID1_UNIT_PATH_PREFIXES


# --- composition --------------------------------------------------------------


async def test_composing_role_captures_nothing_but_still_works():
    """``yield from scoped(...)`` returns ``None``: the role publishes no outputs.

    Both paths return -- the target host and the family-guarded one -- so a
    parent that embeds the role cannot crash on a skipped host, and its own
    requests keep their scope label in the tree.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    v = resolve_vars(SystemdVars, "systemd", inventory)
    captured: dict[str, Any] = {}

    def parent(host: SimulatedHost):
        outputs = yield from scoped("systemd", systemd(v))(host)
        captured[host.name] = outputs
        yield from ()

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    report, _hosts = await run_waves(inventory, parent, host_factory=factory)

    assert captured == {"ubuntu": None, "web1": None}
    assert "systemd" in report.tree()
    assert entry_named(report, HANDLER_NAMES[0]).changed


# --- the wave tree (§7.2) -----------------------------------------------------

# Pinned from a real simulated run (``pytest tests/roles/test_systemd.py -k
# snapshot`` prints the diff against them). The install spool directories are
# content-addressed, so the hashes are stable; the ``fact(Users)`` barrier line
# carries the raw passwd dictionary the body received back.
FIRST_PASS_TREE = """(root)
  wave 0
    ubuntu: Render systemd unit file /etc/systemd/system/reemote-service.service [changed: content /etc/systemd/system/reemote-service.service is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-43bf79fdf193ed18/content
        $ sudo sh -c 'mkdir -p /etc/systemd/system && cp /tmp/reemote-install-43bf79fdf193ed18/content /etc/systemd/system/reemote-service.service && chown root:root /etc/systemd/system/reemote-service.service && chmod 644 /etc/systemd/system/reemote-service.service && rm -rf /tmp/reemote-install-43bf79fdf193ed18'
    ubuntu: Render systemd unit file /etc/systemd/system/reemote-socket.socket [changed: content /etc/systemd/system/reemote-socket.socket is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-a0163869554693b1/content
        $ sudo sh -c 'mkdir -p /etc/systemd/system && cp /tmp/reemote-install-a0163869554693b1/content /etc/systemd/system/reemote-socket.socket && chown root:root /etc/systemd/system/reemote-socket.socket && chmod 644 /etc/systemd/system/reemote-socket.socket && rm -rf /tmp/reemote-install-a0163869554693b1'
    ubuntu: Create unit configuration path (/etc/systemd/system/reemote-service.service.d) [changed: /etc/systemd/system/reemote-service.service.d missing, will create]
        $ sudo mkdir -p /etc/systemd/system/reemote-service.service.d
        $ sudo chmod 755 /etc/systemd/system/reemote-service.service.d
        $ sudo chown root:root /etc/systemd/system/reemote-service.service.d
    ubuntu: Render systemd drop-in file /etc/systemd/system/reemote-service.service.d/reemote-override.conf [changed: content /etc/systemd/system/reemote-service.service.d/reemote-override.conf is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-a64a1f479bc58c80/content
        $ sudo sh -c 'mkdir -p /etc/systemd/system/reemote-service.service.d && cp /tmp/reemote-install-a64a1f479bc58c80/content /etc/systemd/system/reemote-service.service.d/reemote-override.conf && chown root:root /etc/systemd/system/reemote-service.service.d/reemote-override.conf && chmod 644 /etc/systemd/system/reemote-service.service.d/reemote-override.conf && rm -rf /tmp/reemote-install-a64a1f479bc58c80'
    barrier: configure done → 1 host notified
  wave 1
    ubuntu: Reload systemd unit reemote-service.service [changed: daemon-reload; reemote-service.service not running (None), will start]
        $ sudo systemctl daemon-reload
        $ sudo systemctl start reemote-service.service
    ubuntu: Reload systemd unit reemote-socket.socket [no change: reemote-socket.socket active state left alone]
    ubuntu: Reload systemd daemon (/etc/systemd/system/reemote-service.service.d/reemote-override.conf) [changed: daemon-reload]
        $ sudo systemctl daemon-reload
    ubuntu: Activate systemd unit reemote-service.service [changed: reemote-service.service already running; reemote-service.service not enabled, will enable]
        $ sudo systemctl enable reemote-service.service
    ubuntu: Activate systemd unit reemote-socket.socket [changed: reemote-socket.socket already stopped; reemote-socket.socket not enabled, will enable]
        $ sudo systemctl enable reemote-socket.socket"""

CONVERGED_TREE = """(root)
  wave 0
    ubuntu: Render systemd unit file /etc/systemd/system/reemote-service.service [no change: content already up to date (/etc/systemd/system/reemote-service.service)]
    ubuntu: Render systemd unit file /etc/systemd/system/reemote-socket.socket [no change: content already up to date (/etc/systemd/system/reemote-socket.socket)]
    ubuntu: Create unit configuration path (/etc/systemd/system/reemote-service.service.d) [no change: directory /etc/systemd/system/reemote-service.service.d already exists]
    ubuntu: Render systemd drop-in file /etc/systemd/system/reemote-service.service.d/reemote-override.conf [no change: content already up to date (/etc/systemd/system/reemote-service.service.d/reemote-override.conf)]
    barrier: configure done → 0 hosts notified
  wave 1
    ubuntu: Activate systemd unit reemote-service.service [no change: reemote-service.service already running; reemote-service.service already enabled]
    ubuntu: Activate systemd unit reemote-socket.socket [no change: reemote-socket.socket already stopped; reemote-socket.socket already enabled]"""

DRY_PREVIEW_TREE = """(root)
  wave 0
    ubuntu: Render systemd unit file /etc/systemd/system/reemote-service.service [changed: content /etc/systemd/system/reemote-service.service is absent, will install]
    ubuntu: Render systemd unit file /etc/systemd/system/reemote-socket.socket [changed: content /etc/systemd/system/reemote-socket.socket is absent, will install]
    ubuntu: Create unit configuration path (/etc/systemd/system/reemote-service.service.d) [changed: /etc/systemd/system/reemote-service.service.d missing, will create]
    ubuntu: Render systemd drop-in file /etc/systemd/system/reemote-service.service.d/reemote-override.conf [changed: content /etc/systemd/system/reemote-service.service.d/reemote-override.conf is absent, will install]
    barrier: configure done → unknown until run"""

UNINSTALL_TREE = """(root)
  wave 0
    ubuntu: Stop and disable systemd unit reemote-service.service [changed: reemote-service.service running, will stop; reemote-service.service enabled, will disable]
        $ sudo systemctl stop reemote-service.service
        $ sudo systemctl disable reemote-service.service
    ubuntu: Stop and disable systemd unit reemote-socket.socket [changed: reemote-socket.socket already stopped; reemote-socket.socket enabled, will disable]
        $ sudo systemctl disable reemote-socket.socket
    ubuntu: Remove systemd unit file /etc/systemd/system/reemote-service.service [changed: /etc/systemd/system/reemote-service.service present, will remove]
        $ sudo rm -f /etc/systemd/system/reemote-service.service
    ubuntu: Remove systemd unit file /etc/systemd/system/reemote-socket.socket [changed: /etc/systemd/system/reemote-socket.socket present, will remove]
        $ sudo rm -f /etc/systemd/system/reemote-socket.socket
    ubuntu: Remove systemd unit file /etc/systemd/system/reemote-service.service.d/reemote-override.conf [changed: /etc/systemd/system/reemote-service.service.d/reemote-override.conf present, will remove]
        $ sudo rm -f /etc/systemd/system/reemote-service.service.d/reemote-override.conf"""

USER_PASS_TREE = """(root)
  wave 0
    barrier: Resolve passwd entries for unit owners → {'root': {'home': '/root', 'comment': None, 'shell': '/bin/bash', 'group': 'root', 'groups': [], 'uid': 0, 'gid': 0, 'lastlog': None, 'login_time': None, 'password': ''}, 'sduser': {'home': '/home/sduser', 'comment': None, 'shell': '/bin/bash', 'group': 'sduser', 'groups': [], 'uid': 1234, 'gid': 1234, 'lastlog': None, 'login_time': None, 'password': ''}}
  wave 1
    ubuntu: Render systemd unit file /etc/systemd/system/reemote-service.service [changed: content /etc/systemd/system/reemote-service.service is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-43bf79fdf193ed18/content
        $ sudo sh -c 'mkdir -p /etc/systemd/system && cp /tmp/reemote-install-43bf79fdf193ed18/content /etc/systemd/system/reemote-service.service && chown root:root /etc/systemd/system/reemote-service.service && chmod 644 /etc/systemd/system/reemote-service.service && rm -rf /tmp/reemote-install-43bf79fdf193ed18'
    ubuntu: Render systemd unit file /etc/systemd/system/reemote-socket.socket [changed: content /etc/systemd/system/reemote-socket.socket is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-a0163869554693b1/content
        $ sudo sh -c 'mkdir -p /etc/systemd/system && cp /tmp/reemote-install-a0163869554693b1/content /etc/systemd/system/reemote-socket.socket && chown root:root /etc/systemd/system/reemote-socket.socket && chmod 644 /etc/systemd/system/reemote-socket.socket && rm -rf /tmp/reemote-install-a0163869554693b1'
    ubuntu: Create unit configuration path (/etc/systemd/system/reemote-service.service.d) [changed: /etc/systemd/system/reemote-service.service.d missing, will create]
        $ sudo mkdir -p /etc/systemd/system/reemote-service.service.d
        $ sudo chmod 755 /etc/systemd/system/reemote-service.service.d
        $ sudo chown root:root /etc/systemd/system/reemote-service.service.d
    ubuntu: Render systemd drop-in file /etc/systemd/system/reemote-service.service.d/reemote-override.conf [changed: content /etc/systemd/system/reemote-service.service.d/reemote-override.conf is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-a64a1f479bc58c80/content
        $ sudo sh -c 'mkdir -p /etc/systemd/system/reemote-service.service.d && cp /tmp/reemote-install-a64a1f479bc58c80/content /etc/systemd/system/reemote-service.service.d/reemote-override.conf && chown root:root /etc/systemd/system/reemote-service.service.d/reemote-override.conf && chmod 644 /etc/systemd/system/reemote-service.service.d/reemote-override.conf && rm -rf /tmp/reemote-install-a64a1f479bc58c80'
    ubuntu: Create unit configuration path (/home/sduser/.config/systemd/user) [changed: /home/sduser/.config/systemd/user missing, will create]
        $ sudo mkdir -p /home/sduser/.config/systemd/user
        $ sudo chmod 755 /home/sduser/.config/systemd/user
        $ sudo chown sduser:sduser /home/sduser/.config/systemd/user
    ubuntu: Render systemd unit file /home/sduser/.config/systemd/user/reemote-job.service [changed: content /home/sduser/.config/systemd/user/reemote-job.service is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-151164e53976bf4f/content
        $ sudo sh -c 'mkdir -p /home/sduser/.config/systemd/user && cp /tmp/reemote-install-151164e53976bf4f/content /home/sduser/.config/systemd/user/reemote-job.service && chown sduser:sduser /home/sduser/.config/systemd/user/reemote-job.service && chmod 644 /home/sduser/.config/systemd/user/reemote-job.service && rm -rf /tmp/reemote-install-151164e53976bf4f'
    barrier: configure done → 1 host notified
  wave 2
    ubuntu: Reload systemd unit reemote-service.service [changed: daemon-reload; reemote-service.service not running (None), will start]
        $ sudo systemctl daemon-reload
        $ sudo systemctl start reemote-service.service
    ubuntu: Reload systemd unit reemote-socket.socket [no change: reemote-socket.socket active state left alone]
    ubuntu: Reload systemd unit reemote-job.service [changed: reemote-job.service not running (None), will start]
        $ sudo env XDG_RUNTIME_DIR=/run/user/1234 systemctl --machine=sduser@.host start reemote-job.service
    ubuntu: Reload systemd daemon (/etc/systemd/system/reemote-service.service.d/reemote-override.conf) [changed: daemon-reload]
        $ sudo systemctl daemon-reload
    ubuntu: Activate systemd unit reemote-service.service [changed: reemote-service.service already running; reemote-service.service not enabled, will enable]
        $ sudo systemctl enable reemote-service.service
    ubuntu: Activate systemd unit reemote-socket.socket [changed: reemote-socket.socket already stopped; reemote-socket.socket not enabled, will enable]
        $ sudo systemctl enable reemote-socket.socket
    ubuntu: Activate systemd unit reemote-job.service [changed: reemote-job.service already running; reemote-job.service not enabled, will enable]
        $ sudo env XDG_RUNTIME_DIR=/run/user/1234 systemctl --machine=sduser@.host enable reemote-job.service"""


async def test_tree_snapshot():
    """The whole run as one tree: files, checkpoint, handlers, launch.

    The second tree is what a rerun prints -- the same two waves, no handler line
    -- and it is the preview ``dry`` deliberately refuses to fake (§16.6).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)

    assert first.tree() == FIRST_PASS_TREE

    second = await run_role(inventory, hosts)
    assert second.tree() == CONVERGED_TREE


async def test_dry_tree_snapshot():
    inventory = make_inventory()
    report = await run_role(inventory, make_hosts(inventory), dry=True)

    assert report.tree() == DRY_PREVIEW_TREE


async def test_uninstall_tree_snapshot():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    report = await run_role(make_inventory(perform_uninstall=True), hosts)
    assert report.tree() == UNINSTALL_TREE


async def test_user_unit_tree_snapshot():
    """The three-wave stream, in one picture: read, files, then both managers.

    The two ``systemctl`` lines of the per-user unit are the visible half of D1
    -- ``--machine=sduser@.host`` with ``XDG_RUNTIME_DIR`` in the environment,
    run as root instead of ``sudo -u sduser systemctl --user``.
    """
    inventory = make_inventory(unit_config=[*UNITS, USER_ENTRY])
    report = await run_role(inventory, make_hosts(inventory))

    assert report.tree() == USER_PASS_TREE
