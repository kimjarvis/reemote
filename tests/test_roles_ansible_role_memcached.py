# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end test for the Memcached Ansible-role migration example.

``examples/roles/ansible-role-memcached/ansible-role-memcached.py``
connects for real when run as a script, so the suite never executes its
``main()``. What it *can* do offline is replay the same orchestration
against ``SimulatedHost``: the example is imported as a module, its
deploy factory is fed the plan snapshot rendered from static inventory
data, and both deploys go through the real driver (prepare + apply, plus
a ``dry=True`` preview).

The load-bearing assertions are the migration's idempotence and handler
claims: run the whole render -> deploy -> notification-read -> handler
sequence, then run it *again* against the same persistent hosts, and the
second pass must report ``changed=False`` everywhere **and** freeze an
empty handler set. The first pass must fire ``restart memcached`` on the
Ubuntu target only -- the two-deploy outcome split the role's
``notify:`` migrates to.

**No between-pass bridge is needed**: the simulation's own mutators
maintain every seam the second pass reads -- ``install_apt_packages``
records the package, ``install_file`` the config content and its full
stat row, ``chmod_path``/``chown_path`` the metadata convergence, the
``SystemctlCommand`` arms the service state and enablement, and the
``AptUpdateCommand`` arm refreshes ``apt_update_stamp`` to wall-clock
now, so the 86400-second cache window skips on the second apply.
"""

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from reemote import changed_hosts, run
from tests.simulated_host import SimulatedHost

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = (
    REPO_ROOT
    / "examples"
    / "roles"
    / "ansible-role-memcached"
    / "ansible-role-memcached.py"
)


def load_example() -> Any:
    """Import the migration example by path (its name is not a valid identifier)."""
    spec = importlib.util.spec_from_file_location(
        "ansible_role_memcached_example", EXAMPLE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


memcached = load_example()

CONFIG_FILE = "/etc/memcached.conf"

#: The role template rendered with every default, byte-for-byte: the
#: frozen ``ansible_managed`` header, the blank line the empty
#: ``memcached_log_verbosity`` leaves, the string-branch ``-l`` line and
#: the kept trailing newline. Written out by hand (not re-rendered) so a
#: template or environment drift cannot hide behind the same renderer.
DEFAULT_CONFIG = """\
# Ansible managed
# memcached default config file
# 2003 - Jay Bonci <jaybonci@debian.org>
# This configuration file is read by the start-memcached script provided as
# part of the Debian GNU/Linux distribution.

# Run memcached as a daemon. This command is implied, and is not needed for the
# daemon to run. See the README.Debian that comes with this package for more
# information.
-d

# Threads count
-t 4

# Log memcached's output to /var/log/memcached
logfile /var/log/memcached.log


# Start with a cap of 64 megs of memory. It's reasonable, and the daemon default
# Note that the daemon will grow to this size, but does not start out holding this much
# memory
-m 64

# Default connection port is 11211
-p 11211

# Run the daemon as root. The start-memcached will default to running as root if no
# -u command is present in this config file
-u memcache

# Specify which IP address to listen on. The default is to listen on all IP addresses
# This parameter is one of the only security measures that memcached has, so make sure
# it's listening on a firewalled interface.
-l 127.0.0.1

# Limit the number of simultaneous incoming connections. The daemon default is 1024
-c 1024

# Lock down all paged memory. Consult with the README and homepage before you do this
# -k

# Return error when memory is exhausted (rather than removing items)
# -M

# Maximize core file limit
# -r

-I 1m
"""


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride as extra keys -- reemote's ``host_vars`` -- so
    ``role_settings`` reads them straight off ``host.extra``. With no
    overrides every ``defaults/main.yml`` value applies.
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


def seed(host: SimulatedHost) -> None:
    """Pre-state: a stock minimal Ubuntu machine with no Memcached installed.

    The apt cache has never been refreshed (the 86400-second window runs),
    no config file exists yet, and the service is neither running nor
    enabled.
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
    host.os_groups = ["root", "sudo"]
    host.os_users = {}
    host.linux_distribution = {
        "name": "Ubuntu",
        "family": "Debian",
        "major": "24",
        "minor": "04",
        "release_meta": {
            "ID": "ubuntu",
            "VERSION_ID": "24.04",
            "VERSION_CODENAME": "noble",
        },
    }


def make_hosts(inventory: list[dict[str, Any]]) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if memcached.is_ubuntu(host):
            seed(host)
        hosts[host.name] = host
    return hosts


@pytest.fixture
def rig() -> tuple[list[dict[str, Any]], dict[str, SimulatedHost]]:
    """An inventory plus the persistent simulated hosts it materializes into."""
    inventory = make_inventory()
    return inventory, make_hosts(inventory)


async def migrate(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SimulatedHost],
    *,
    dry: bool = True,
) -> dict[str, Any]:
    """Replay the example's whole orchestration once, against persistent hosts.

    The sequence mirrors ``main()`` barrier for barrier -- the variable
    gates, the control-side render, the factory-built main deploy, the
    ``changed_hosts`` notification read and the ``restart_handler``
    deploy -- but reads the simulated hosts instead of opening SSH
    connections. No fact gather happens: every value is static.
    """
    targets = [host for host in hosts.values() if memcached.is_ubuntu(host)]
    settings = {host.name: memcached.role_settings(host) for host in targets}
    plans = {name: memcached.memcached_plan(settings[name]) for name in settings}
    reports: list[Any] = []

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        # The *same* host object every run, so state survives the barriers.
        return hosts[entry["host"]]

    async def execute(deploy: Any) -> Any:
        if dry:
            await run(inventory, deploy, host_factory=factory, dry=True)
        _plans, report, _hosts = await run(inventory, deploy, host_factory=factory)
        assert report is not None
        reports.append(report)
        return report

    report = await execute(memcached.build_memcached(plans))
    restart_notified = changed_hosts(report, memcached.NOTIFY_RESTART)
    await execute(
        memcached.restart_handler(restart_notified, "memcached", when=memcached.is_ubuntu)
    )

    return {
        "reports": reports,
        "settings": settings,
        "plans": plans,
        "restart_notified": restart_notified,
    }


def changed_entries(result: dict[str, Any]) -> list[Any]:
    """Every changed log entry across the whole sequence."""
    return [entry for report in result["reports"] for entry in report.log if entry.changed]


# --- the first pass really deploys the role ----------------------------------


async def test_first_pass_installs_configures_and_notifies(rig):
    inventory, hosts = rig
    result = await migrate(inventory, hosts)
    host = hosts["ubuntu"]

    # The package and the folded cache refresh (the stamp was absent).
    assert "memcached" in host.apt_packages
    assert host.apt_update_stamp is not None

    # The config: the default render, root-owned 0644, full stat row.
    assert host.remote_files[CONFIG_FILE] == DEFAULT_CONFIG.encode()
    assert host.file_stats[CONFIG_FILE] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(DEFAULT_CONFIG.encode()),
    }

    # The service is started and enabled (the final task, no notify).
    assert host.services["memcached.service"] == "active"
    assert "memcached.service" in host.enabled_services

    # The notifying install request really changed something.
    assert any(
        entry.name == "Copy Memcached configuration" and entry.changed
        for entry in result["reports"][0].log
    )

    # The handler fired on the Ubuntu target only, after the main deploy.
    assert result["restart_notified"] == frozenset({"ubuntu"})
    handler_log = result["reports"][1].log
    assert [entry.name for entry in handler_log] == ["Restart memcached (handler)"]
    assert all(entry.changed and entry.host == "ubuntu" for entry in handler_log)

    # The whole migration is operations: no shell action ever ran.
    assert host.ran_shells == []


async def test_non_ubuntu_host_is_never_touched(rig):
    inventory, hosts = rig
    result = await migrate(inventory, hosts)

    skipped = hosts["web1"]
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}
    assert skipped.services == {}
    assert skipped.ran_shells == []
    # The factory-built deploy yields no request at all for a host without
    # a plan, and the handler set never names it; any entry that did reach
    # the log must be a skipped one.
    for report in result["reports"]:
        for entry in report.log:
            if entry.host == "web1":
                assert entry.skipped
                assert not entry.changed


# --- the second pass is a no-op ----------------------------------------------


async def test_second_pass_changes_nothing_and_queues_no_handler(rig):
    inventory, hosts = rig
    first = await migrate(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    # No bridge is needed: every seam the second pass reads is maintained
    # by the simulation's own mutators (see the module docstring).
    second = await migrate(inventory, hosts)

    assert changed_entries(second) == []
    assert second["restart_notified"] == frozenset()
    assert second["reports"][1].log == []
    assert hosts["ubuntu"].ran_shells == []


async def test_dry_preview_mutates_nothing(rig):
    """``dry=True`` stops after prepare, so it must not touch the target."""
    inventory, hosts = rig
    host = hosts["ubuntu"]

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    # The plan main() renders control-side, frozen the same way.
    plans = {
        "ubuntu": memcached.memcached_plan(memcached.role_settings(host)),
    }
    await run(inventory, memcached.build_memcached(plans), host_factory=factory, dry=True)

    assert host.remote_files == {}
    assert host.existing_files == set()
    assert host.file_stats == {}
    assert host.apt_packages == {}
    assert host.apt_update_stamp is None
    assert host.services == {}
    assert host.enabled_services == set()


# --- the override branches ----------------------------------------------------


async def test_listen_ip_list_renders_one_line_per_address():
    """The template's else-branch: a list renders one ``-l`` line per item."""
    inventory = make_inventory(memcached_listen_ip=["127.0.0.1", "192.0.2.10"])
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    result = await migrate(inventory, hosts)

    expected = DEFAULT_CONFIG.replace(
        "-l 127.0.0.1\n", "-l 127.0.0.1\n-l 192.0.2.10\n"
    )
    assert host.remote_files[CONFIG_FILE] == expected.encode()
    assert result["restart_notified"] == frozenset({"ubuntu"})

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


async def test_user_and_verbosity_overrides_ride_host_extra():
    """``memcached_user`` beats the vars-table default; ``-v`` fills the blank line."""
    inventory = make_inventory(memcached_user="daemon", memcached_log_verbosity="-v")
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    await migrate(inventory, hosts)

    expected = DEFAULT_CONFIG.replace("-u memcache\n", "-u daemon\n").replace(
        "logfile /var/log/memcached.log\n\n\n",
        "logfile /var/log/memcached.log\n-v\n\n",
    )
    assert host.remote_files[CONFIG_FILE] == expected.encode()

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


# --- the metadata companion and its notification ------------------------------


async def test_metadata_drift_converges_and_notifies(rig):
    """Right content, wrong mode: the companion converges it and queues the restart.

    ``files.install`` is content-idempotent, so the install request stays
    quiet; the paired ``files.file`` reports the mode change -- and its
    name notifies, because Ansible's template module reports a change on
    owner/group/mode drift too (deviation D3).
    """
    inventory, hosts = rig
    host = hosts["ubuntu"]
    host.remote_files[CONFIG_FILE] = DEFAULT_CONFIG.encode()
    host.existing_files.add(CONFIG_FILE)
    host.file_stats[CONFIG_FILE] = {
        "user": "root",
        "group": "root",
        "mode": 600,
        "size": len(DEFAULT_CONFIG.encode()),
    }

    result = await migrate(inventory, hosts)

    main_log = result["reports"][0].log
    assert any(
        entry.name == "Set Memcached configuration attributes" and entry.changed
        for entry in main_log
    )
    assert not any(
        entry.name == "Copy Memcached configuration" and entry.changed
        for entry in main_log
    )
    assert host.file_stats[CONFIG_FILE]["mode"] == 644
    assert result["restart_notified"] == frozenset({"ubuntu"})

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []
    assert second["restart_notified"] == frozenset()


async def test_content_drift_reinstalls_and_notifies(rig):
    """Stale content: the install request rewrites the file and queues the restart."""
    inventory, hosts = rig
    host = hosts["ubuntu"]
    host.remote_files[CONFIG_FILE] = b"# stale config\n"
    host.existing_files.add(CONFIG_FILE)
    host.file_stats[CONFIG_FILE] = {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(b"# stale config\n"),
    }

    result = await migrate(inventory, hosts)

    assert any(
        entry.name == "Copy Memcached configuration" and entry.changed
        for entry in result["reports"][0].log
    )
    assert host.remote_files[CONFIG_FILE] == DEFAULT_CONFIG.encode()
    assert result["restart_notified"] == frozenset({"ubuntu"})

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


# --- the pure helpers, unit-tested directly -----------------------------------


def test_render_matches_the_template_defaults():
    """The Jinja2 render is the template source with every default filled."""
    host = SimulatedHost.from_inventory(make_inventory()[0])
    rendered = memcached.render_memcached_conf(memcached.role_settings(host))
    assert rendered == DEFAULT_CONFIG
    assert rendered.endswith("-I 1m\n")


def test_render_listen_ip_shapes():
    """The str-or-sequence shape: one line, many lines, or none at all."""
    host = SimulatedHost.from_inventory(make_inventory()[0])
    settings = memcached.role_settings(host)

    settings["memcached_listen_ip"] = ("127.0.0.1", "192.0.2.10")
    assert memcached.render_memcached_conf(settings) == DEFAULT_CONFIG.replace(
        "-l 127.0.0.1\n", "-l 127.0.0.1\n-l 192.0.2.10\n"
    )

    # An empty list is a valid Ansible override: no -l line at all, so
    # memcached listens on every interface (the template's comment).
    settings["memcached_listen_ip"] = ()
    assert memcached.render_memcached_conf(settings) == DEFAULT_CONFIG.replace(
        "-l 127.0.0.1\n", ""
    )


def test_role_settings_defaults_and_overrides():
    host = SimulatedHost.from_inventory(make_inventory()[0])
    settings = memcached.role_settings(host)
    assert settings["memcached_port"] == 11211
    assert settings["memcached_listen_ip"] == "127.0.0.1"
    assert settings["memcached_memory_limit"] == 64
    assert settings["memcached_connections"] == 1024
    assert settings["memcached_log_file"] == "/var/log/memcached.log"
    assert settings["memcached_log_verbosity"] == ""
    assert settings["memcached_max_item_size"] == "1m"
    assert settings["memcached_threads"] == 4
    # The set_fact default: vars/Debian.yml's __memcached_user.
    assert settings["memcached_user"] == "memcache"

    overridden = SimulatedHost.from_inventory(
        make_inventory(memcached_user="daemon", memcached_port=11212)[0]
    )
    settings = memcached.role_settings(overridden)
    assert settings["memcached_user"] == "daemon"
    assert settings["memcached_port"] == 11212

    listed = SimulatedHost.from_inventory(
        make_inventory(memcached_listen_ip=["127.0.0.1", "192.0.2.10"])[0]
    )
    assert memcached.role_settings(listed)["memcached_listen_ip"] == (
        "127.0.0.1",
        "192.0.2.10",
    )


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"memcached_port": "11211"}, "memcached_port"),
        ({"memcached_memory_limit": "64"}, "memcached_memory_limit"),
        ({"memcached_connections": 1024.0}, "memcached_connections"),
        ({"memcached_threads": "4"}, "memcached_threads"),
        ({"memcached_user": 42}, "memcached_user"),
        ({"memcached_log_file": None}, "memcached_log_file"),
        ({"memcached_log_verbosity": 1}, "memcached_log_verbosity"),
        ({"memcached_max_item_size": 1}, "memcached_max_item_size"),
        ({"memcached_listen_ip": 42}, "memcached_listen_ip"),
        ({"memcached_listen_ip": [42]}, "memcached_listen_ip"),
    ],
)
def test_invalid_role_values_fail_fast(overrides, match):
    host = SimulatedHost.from_inventory(make_inventory(**overrides)[0])
    with pytest.raises(TypeError, match=match):
        memcached.role_settings(host)