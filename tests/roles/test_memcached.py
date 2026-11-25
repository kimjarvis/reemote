# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``memcached`` role (§17.5, §18.13).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, memcached(v), host_factory=...)``. The factory is plain
(non-``async``) -- every input is a resolved variable or a frozen constant --
so it is called, not awaited.

The load-bearing assertion is the migration's shape claim from §18.13: the
stream is **two waves** and the checkpoint is the **only barrier** -- the
configure requests, then ``checkpoint("configure done")`` closing that wave,
then the handler wave for the notified hosts. No ``fact``, no ``act``, no
``control``: a speculative barrier would break the shape test, which reads the
records straight out of ``report.records``. On a converged rerun nothing
notifies, so the second request wave never appears at all (one wave, not two).

**No between-pass bridge is needed**: the simulation's own mutators maintain
every seam the second pass reads -- ``install_apt_packages`` records the
package, ``install_file`` the config content and its full stat row,
``chmod_path``/``chown_path`` the metadata convergence, the ``SystemctlCommand``
arms the service state and enablement, and the ``AptUpdateCommand`` arm
refreshes ``apt_update_stamp`` to wall-clock now, so the 86400-second cache
window skips on the second apply.

The config file needs no pre-seed: ``files.install``'s spool-and-install mkdirs
its parent and the content-idempotent write converges on the second pass. The
drift tests do pre-seed it (right content, wrong mode, and stale content) to
drive the two notification paths the companion and the install each own.
"""

import pytest
from pydantic import ValidationError

from reemote import run_waves
from reemote.roles import MemcachedVars, memcached
from reemote.roles.memcached import (
    CACHE_VALID_TIME,
    CONFIG_FILE,
    DEFAULT_USER,
    NOTIFY_RESTART,
    SERVICE,
    MemcachedPlan,
    memcached_plan,
    render_memcached_conf,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from reemote.stages import CheckpointRequest, FactRequest
from tests.simulated_host import SimulatedHost

#: The handler request keeps ``restart_handler``'s default label, not a name of
#: the role's own -- the notification is read from the notifying request names,
#: the handler logs under the library's generated one.
HANDLER_NAME = "Restart memcached (handler)"

#: The role template rendered with every default, byte-for-byte: the frozen
#: ``ansible_managed`` header, the blank line the empty
#: ``memcached_log_verbosity`` leaves, the string-branch ``-l`` line and the
#: kept trailing newline. Written out by hand (not re-rendered) so a template or
#: environment drift cannot hide behind the same renderer.
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


def make_inventory(**overrides) -> list[dict]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride as ``memcached_``-prefixed extra keys, bound by
    ``resolve_vars``. With no overrides every ``defaults/main.yml`` value
    applies, so the render is :data:`DEFAULT_CONFIG`.
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


def seed(host: SimulatedHost) -> None:
    """Pre-state: a stock minimal Ubuntu machine with no Memcached installed.

    The apt cache has never been refreshed (so the 86400-second window runs),
    no config file exists yet, and the service is neither running nor enabled.
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
    v = resolve_vars(MemcachedVars, "memcached_", inventory)

    def factory(entry: dict) -> SimulatedHost:
        # The *same* host object every pass, so state survives the barriers.
        return hosts[entry["host"]]

    # A plain (non-async) factory: called, not awaited.
    deploy = memcached(v)
    report, _hosts = await run_waves(inventory, deploy, host_factory=factory, dry=dry)
    return report


def changed_entries(report) -> list:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def ubuntu_names(report) -> set[str]:
    """Every Ubuntu log entry name of one pass."""
    return {entry.name for entry in report.merged().log if entry.host == "ubuntu"}


def ubuntu_log(report) -> dict[str, object]:
    """The Ubuntu host's log entries of one pass, keyed by name."""
    return {
        entry.name: entry for entry in report.merged().log if entry.host == "ubuntu"
    }


def _kind(barrier) -> tuple:
    """Classify one barrier record for the shape assertion.

    A ``fact`` barrier names the fact class it reads; a ``checkpoint`` names
    itself. An ``act``/``control`` barrier would fall through to a distinct
    label, so a speculatively-added barrier breaks the shape test rather than
    hiding.
    """
    if isinstance(barrier, FactRequest):
        return ("fact", barrier.fact_cls)
    if isinstance(barrier, CheckpointRequest):
        return ("checkpoint", barrier.name)
    return ("other", type(barrier).__name__)


def _barrier_shape(report):
    """The ubuntu host's ordered ``(wave, kind)`` barrier list for one pass."""
    ubuntu = [r for r in report.records if r.host == "ubuntu"]
    return [(r.wave, _kind(r.barrier)) for r in ubuntu if r.barrier is not None]


def _request_waves(report) -> set[int]:
    """The set of wave numbers that carried at least one ubuntu request."""
    return {
        r.wave
        for r in report.records
        if r.host == "ubuntu" and r.log is not None
    }


# --- the stream shape (§18.13's falsifier) ------------------------------------


async def test_stream_is_one_checkpoint_and_two_waves():
    """§18.13's shape: the requests, one checkpoint, then the handler wave.

    Exactly one ``checkpoint`` barrier and **no fact/act/control at all** -- the
    plan is entirely static, so anything else would be a barrier added
    speculatively. Requests land in two waves (configure 0, handler 1).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert _barrier_shape(report) == [(0, ("checkpoint", "configure done"))]
    assert _request_waves(report) == {0, 1}

    # The rerun re-derives the same barrier; nothing notifies, so no second
    # request wave appears at all.
    second = await run_role(inventory, hosts)
    assert _barrier_shape(second) == _barrier_shape(report)
    assert _request_waves(second) == {0}


# --- the first pass really deploys the role ----------------------------------


async def test_first_pass_installs_configures_and_notifies():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    # The package and the 86400-second-window cache refresh (the stamp was absent).
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

    # The checkpoint read the config change, so the handler fired on this host.
    log = ubuntu_log(report)
    assert log["Copy Memcached configuration"].changed
    assert log[HANDLER_NAME].changed
    assert log[HANDLER_NAME].host == "ubuntu"

    # The whole migration is operations: no shell action ever ran.
    assert host.ran_shells == []


async def test_companion_is_quiet_when_install_wrote_the_metadata():
    """``files.install`` writes content *and* mode, so the companion no-ops.

    The companion's purpose is drift on an otherwise-converged file (the java
    precedent), not a first-pass double write.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert ubuntu_log(report)["Set Memcached configuration attributes"].changed is False


async def test_second_pass_is_a_noop_with_no_handler_wave():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    # The migration's idempotence claim: nothing changed anywhere and no handler
    # ran -- stamp fresh, package present, config content-equal with the right
    # metadata, service running and enabled, so nobody notified.
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert HANDLER_NAME not in ubuntu_names(second)
    assert hosts["ubuntu"].ran_shells == []


async def test_non_ubuntu_host_is_never_touched():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    skipped = hosts["web1"]

    # The family guard returns before the first yield, so the off-target host is
    # in no wave, emits no log line and its stores never move.
    assert not any(entry.host == "web1" for entry in report.merged().log)
    assert not any(r.host == "web1" for r in report.records)
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}
    assert skipped.services == {}
    assert skipped.ran_shells == []


async def test_dry_preview_plans_the_requests_and_parks_at_the_checkpoint():
    """``dry=True`` never runs a barrier, so the handler wave stays unplanned.

    The role's first barrier is the checkpoint *after* the request wave, so --
    unlike a role that opens with a ``fact`` -- the dry preview does plan all
    five configure requests (visible in the tree) and stops there. Nothing is
    applied and no notification is reported.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts, dry=True)

    assert host.apt_packages == {}
    assert host.apt_update_stamp is None
    assert host.remote_files == {}
    assert host.services == {}
    assert changed_entries(report) == []
    # The planned request records carry no apply log; one barrier record, at
    # wave 0, and no report for it to read.
    assert _barrier_shape(report) == [(0, ("checkpoint", "configure done"))]
    assert _request_waves(report) == set()
    assert report.merged().log == []


# --- the two notification paths ------------------------------------------------


async def test_metadata_drift_converges_and_notifies():
    """Right content, wrong mode: the companion converges it and queues the restart.

    ``files.install`` is content-idempotent, so the install request stays quiet;
    the paired ``files.file`` reports the mode change -- and its name notifies,
    because Ansible's template reports a change on owner/group/mode drift too
    (D3).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    host.remote_files[CONFIG_FILE] = DEFAULT_CONFIG.encode()
    host.existing_files.add(CONFIG_FILE)
    host.file_stats[CONFIG_FILE] = {
        "user": "root",
        "group": "root",
        "mode": 600,
        "size": len(DEFAULT_CONFIG.encode()),
    }

    report = await run_role(inventory, hosts)
    log = ubuntu_log(report)

    assert log["Copy Memcached configuration"].changed is False
    assert log["Set Memcached configuration attributes"].changed
    assert log[HANDLER_NAME].changed
    assert host.file_stats[CONFIG_FILE]["mode"] == 644

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert HANDLER_NAME not in ubuntu_names(second)


async def test_content_drift_reinstalls_and_notifies():
    """Stale content: the install request rewrites the file and queues the restart."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    host.remote_files[CONFIG_FILE] = b"# stale config\n"
    host.existing_files.add(CONFIG_FILE)
    host.file_stats[CONFIG_FILE] = {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(b"# stale config\n"),
    }

    report = await run_role(inventory, hosts)
    log = ubuntu_log(report)

    assert log["Copy Memcached configuration"].changed
    assert log[HANDLER_NAME].changed
    assert host.remote_files[CONFIG_FILE] == DEFAULT_CONFIG.encode()

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the override branches ------------------------------------------------------


async def test_listen_ip_list_renders_one_line_per_address():
    """The template's else-branch: a list renders one ``-l`` line per item."""
    inventory = make_inventory(memcached_listen_ip=["127.0.0.1", "192.0.2.10"])
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    await run_role(inventory, hosts)

    expected = DEFAULT_CONFIG.replace(
        "-l 127.0.0.1\n", "-l 127.0.0.1\n-l 192.0.2.10\n"
    )
    assert host.remote_files[CONFIG_FILE] == expected.encode()

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_empty_listen_ip_renders_no_binding_line():
    """An empty list is a valid Ansible override: no ``-l`` line at all."""
    inventory = make_inventory(memcached_listen_ip=[])
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    await run_role(inventory, hosts)

    assert b"-l " not in host.remote_files[CONFIG_FILE]
    assert host.remote_files[CONFIG_FILE] == DEFAULT_CONFIG.replace(
        "-l 127.0.0.1\n", ""
    ).encode()


async def test_user_and_verbosity_overrides_ride_the_inventory():
    """``memcached_user`` beats the vars-table default; ``-v`` fills the blank line."""
    inventory = make_inventory(memcached_user="daemon", memcached_log_verbosity="-v")
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    await run_role(inventory, hosts)

    expected = DEFAULT_CONFIG.replace("-u memcache\n", "-u daemon\n").replace(
        "logfile /var/log/memcached.log\n\n\n",
        "logfile /var/log/memcached.log\n-v\n\n",
    )
    assert host.remote_files[CONFIG_FILE] == expected.encode()

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the notification channel ---------------------------------------------------


def test_only_the_config_requests_notify():
    """The package, cache-refresh and service names are deliberately not listeners.

    The role's one ``notify:`` sits on the template task; the companion joins it
    for metadata drift (D3). Nothing else in :data:`NOTIFY_RESTART`.
    """
    assert NOTIFY_RESTART == frozenset(
        {
            "Copy Memcached configuration",
            "Set Memcached configuration attributes",
        }
    )
    assert "Install Memcached" not in NOTIFY_RESTART
    assert "Update apt cache" not in NOTIFY_RESTART
    assert "Ensure Memcached is started and enabled" not in NOTIFY_RESTART


def test_frozen_constants_are_the_debian_vars_table():
    """``vars/Debian.yml`` collapsed to constants: the destination and the user."""
    assert CONFIG_FILE == "/etc/memcached.conf"
    assert DEFAULT_USER == "memcache"
    assert CACHE_VALID_TIME == 86400
    assert SERVICE == "memcached"


# --- the pure helpers, unit-tested directly --------------------------------------


def test_render_matches_the_template_defaults():
    """The Jinja2 render is the template source with every default filled."""
    rendered = render_memcached_conf(MemcachedVars())
    assert rendered == DEFAULT_CONFIG
    assert rendered.endswith("-I 1m\n")


def test_render_listen_ip_shapes():
    """The str-or-sequence shape: one line, many lines, or none at all."""
    defaults = render_memcached_conf(MemcachedVars())

    listed = render_memcached_conf(MemcachedVars(listen_ip=("127.0.0.1", "192.0.2.10")))
    assert listed == defaults.replace("-l 127.0.0.1\n", "-l 127.0.0.1\n-l 192.0.2.10\n")

    empty = render_memcached_conf(MemcachedVars(listen_ip=()))
    assert empty == defaults.replace("-l 127.0.0.1\n", "")


def test_memcached_plan_freezes_the_render():
    """The plan is the one rendered string, frozen and hashable (no remote read)."""
    plan = memcached_plan(MemcachedVars(memory_limit=128))
    assert isinstance(plan, MemcachedPlan)
    assert plan.config_content == render_memcached_conf(MemcachedVars(memory_limit=128))
    assert "-m 128\n" in plan.config_content
    assert hash(plan) == hash(MemcachedPlan(config_content=plan.config_content))


def test_model_is_frozen_and_hashable():
    v = MemcachedVars()
    assert hash(v) is not None
    with pytest.raises(ValidationError):
        v.port = 11212


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"memcached_user": 42}, "user"),
        ({"memcached_log_file": None}, "log_file"),
        ({"memcached_log_verbosity": 1}, "log_verbosity"),
        ({"memcached_max_item_size": 1}, "max_item_size"),
        ({"memcached_listen_ip": 42}, "listen_ip"),
        ({"memcached_listen_ip": [42]}, "listen_ip"),
        ({"memcached_user": ""}, "user"),
        ({"memcached_max_item_size": ""}, "max_item_size"),
    ],
)
def test_invalid_role_values_fail_fast(overrides, match):
    """The old example's ``TypeError`` gates are pydantic rejections now (D5)."""
    inventory = make_inventory(**overrides)
    with pytest.raises(ValidationError, match=match):
        resolve_vars(MemcachedVars, "memcached_", inventory)


def test_numeric_strings_coerce_into_the_integer_fields():
    """The one widening over the old example: pydantic's lax int coercion (D5).

    ``"11211"`` binds to ``port`` as ``11211`` (the repo-wide convention, as in
    ``ApacheVars.listen_port``) and renders identically, so the fail-fast claim
    covers *unreadable* values, not merely differently-typed ones.
    """
    v = resolve_vars(MemcachedVars, "memcached_", make_inventory(memcached_port="11212"))
    assert v["ubuntu"].port == 11212
    assert "-p 11212\n" in render_memcached_conf(v["ubuntu"])


def test_prefixed_inventory_keys_bind_and_typo_raises():
    """``memcached_`` keys strip onto fields; a prefixed typo raises RoleVarsError."""
    inventory = make_inventory(
        memcached_threads=8, memcached_listen_ip=["127.0.0.1", "192.0.2.10"]
    )
    v = resolve_vars(MemcachedVars, "memcached_", inventory)
    assert v["ubuntu"].threads == 8
    # A JSON list arrives normalized to a hashable tuple (§8.1).
    assert v["ubuntu"].listen_ip == ("127.0.0.1", "192.0.2.10")

    with pytest.raises(RoleVarsError, match="memcached_packagez"):
        resolve_vars(MemcachedVars, "memcached_", make_inventory(memcached_packagez="x"))
