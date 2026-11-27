# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``redis`` role (§17.5).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, redis(v), host_factory=...)``. The factory is plain
(non-``async``) -- every input is a resolved variable or a frozen constant --
so it is called, not awaited.

The load-bearing assertion is the migration's shape claim: the stream is
**two waves** and the checkpoint is the **only barrier** -- the configure
requests, then ``checkpoint("configure done")`` closing that wave, then the
handler wave for the notified hosts. No ``fact``, no ``act``, no ``control``:
a speculative barrier would break the shape test, which reads the records
straight out of ``report.records``. On a converged rerun nothing notifies, so
the second request wave never appears at all (one wave, not two). The Debian
configure-before-install ordering is pinned the same way: within the one wave
the config requests precede the package request.

**No between-pass bridge is needed**: the simulation's own mutators maintain
every seam the second pass reads -- ``install_apt_packages`` records the
package, ``make_directory``/``chmod_path`` the ``02770`` config-directory row,
``install_file`` the config content and its full stat row, the ``SystemctlCommand``
arms the service state and enablement. The ``0640`` service-owned config is
unreadable -- and, inside the ``02770`` directory, invisible -- to the login
user, so the second pass rides
``FileContents``' two-tier elevated read-back: the shipped #12 unreadable
fallback and issue #70's traversal gate, exercised here offline.

The config file needs no pre-seed: ``files.install``'s spool-and-install
lands the content and the content-idempotent write converges on the second
pass. The drift tests do pre-seed it (right content, wrong mode, and stale
content) to drive the two notification paths the companion and the install
each own.
"""

import pytest
from pydantic import ValidationError

from reemote import run_waves
from reemote.roles import RedisVars, redis
from reemote.roles.redis import (
    CONF_DIR,
    CONF_DIR_MODE,
    CONF_GROUP,
    CONF_MODE,
    CONF_PATH,
    CONF_USER,
    NOTIFY_RESTART,
    PACKAGE_DEFAULT,
    SERVICE,
    RedisPlan,
    redis_plan,
    render_redis_conf,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from reemote.stages import CheckpointRequest, FactRequest
from tests.simulated_host import SimulatedHost

#: The handler request keeps ``restart_handler``'s default label, not a name of
#: the role's own -- the notification is read from the notifying request names,
#: the handler logs under the library's generated one.
HANDLER_NAME = "Restart redis-server (handler)"

#: The role template rendered with every default, byte-for-byte: the frozen
#: ``ansible_managed`` header, the double blank lines the false ``{% if %}``
#: guards leave, the default three ``save`` lines and the five empty lines the
#: empty loops and the blank ``redis_extra_config`` produce at the tail.
#: Verified against a render of the upstream ``redis.conf.j2`` through the same
#: environment (so a template or environment drift cannot hide).
DEFAULT_CONFIG = """\
# Ansible managed

daemonize yes
pidfile /var/run/redis/redis-server.pid
port 6379
bind 127.0.0.1


timeout 300

loglevel notice
logfile /var/log/redis/redis-server.log

# To enable logging to the system logger, just set 'syslog-enabled' to yes,
# and optionally update the other syslog parameters to suit your needs.
# syslog-enabled no
# syslog-ident redis
# syslog-facility local0

databases 16

save 900 1
save 300 10
save 60 10000

rdbcompression yes
dbfilename dump.rdb
dir /var/lib/redis

# maxclients 128


appendonly no
appendfsync everysec
no-appendfsync-on-rewrite no





"""


def make_inventory(**overrides) -> list[dict]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride in the entry's ``roles`` mapping under ``"redis"``,
    bound by ``resolve_vars``. With no overrides every ``defaults/main.yml``
    value applies, so the render is :data:`DEFAULT_CONFIG`.
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
        "roles": {
            "redis": {
            },
        },
    }
    entry["roles"]["redis"].update(overrides)
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
    """Pre-state: a stock minimal Ubuntu machine with no Redis installed.

    Nothing redis-related exists yet: no package, no ``/etc/redis`` directory
    row, no config file, and the service is neither running nor enabled. The
    apt cache state is irrelevant here -- the role's Debian path carries no
    ``update_cache`` -- but is zeroed so a stray update command would show up.
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
    v = resolve_vars(RedisVars, "redis", inventory)

    def factory(entry: dict) -> SimulatedHost:
        # The *same* host object every pass, so state survives the barriers.
        return hosts[entry["host"]]

    # A plain (non-async) factory: called, not awaited.
    deploy = redis(v)
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


def _wave_order(report) -> list[str]:
    """The ubuntu host's wave-0 request names, in stream order."""
    return [
        entry.name
        for r in report.records
        if r.host == "ubuntu" and r.wave == 0 and r.log is not None
        for entry in ([r.log] if not isinstance(r.log, list) else r.log)
    ]


# --- the stream shape ---------------------------------------------------------


async def test_stream_is_one_checkpoint_and_two_waves():
    """The role's shape: the requests, one checkpoint, then the handler wave.

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


async def test_configure_requests_precede_the_install():
    """The Debian ordering: config dir, config, companion, package, service.

    ``tasks/main.yml`` runs ``configure.yml`` before ``setup-Debian.yml`` on
    Debian because the package install starts Redis against the managed config;
    the single wave reproduces that order verbatim.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    wave0 = [entry.name for entry in report.merged().log if entry.host == "ubuntu"]
    assert wave0 == [
        "Ensure Redis configuration directory",
        "Configure Redis",
        "Set Redis configuration attributes",
        "Install Redis",
        "Ensure Redis is started and enabled",
        HANDLER_NAME,
    ]


# --- the first pass really deploys the role -----------------------------------


async def test_first_pass_installs_configures_and_notifies():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    # The package (no cache refresh: the role's Debian path has none).
    assert "redis-server" in host.apt_packages
    assert host.apt_update_stamp is None

    # The config directory: created, then chmod'd to the setgid 02770 row, and
    # redis:redis so the daemon's own directory (D6).
    assert host.directory_stats[CONF_DIR] == {
        "mode": 2770,
        "user": "redis",
        "group": "redis",
    }

    # The config: the default render, redis:redis 0640 (readable by the
    # User=redis daemon, D6), full stat row.
    assert host.remote_files[CONF_PATH] == DEFAULT_CONFIG.encode()
    assert host.file_stats[CONF_PATH] == {
        "user": "redis",
        "group": "redis",
        "mode": 640,
        "size": len(DEFAULT_CONFIG.encode()),
    }

    # The service is started and enabled (the final task, no notify).
    assert host.services["redis-server.service"] == "active"
    assert "redis-server.service" in host.enabled_services

    # The checkpoint read the config change, so the handler fired on this host.
    log = ubuntu_log(report)
    assert log["Configure Redis"].changed
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

    assert ubuntu_log(report)["Set Redis configuration attributes"].changed is False


async def test_second_pass_is_a_noop_with_no_handler_wave():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    # The migration's idempotence claim: nothing changed anywhere and no handler
    # ran -- package present, directory 02770, config content-equal (through the
    # two-tier elevated read-back of the 0640 file) with the right metadata,
    # service running and enabled, so nobody notified.
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert HANDLER_NAME not in ubuntu_names(second)
    assert hosts["ubuntu"].ran_shells == []


async def test_the_hidden_config_stays_converged_through_the_elevated_probe():
    """Issue #70's live churn, pinned offline.

    The role's own ``02770 redis:redis`` directory hides ``/etc/redis/redis.conf``
    from the ``supervisor`` login's probe -- not merely from its reads: without the
    directory's ``x`` bit no name inside resolves, so ``test -e`` fails with
    ``EACCES`` and prints the shell's "no such file". A fact that trusted that
    answer would plan the write again on every pass, which is exactly what the
    live run did. The traversal gate turns the absence into an elevated re-probe,
    so the second pass still converges.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # The converged state is there and the login cannot see it.
    assert host.remote_files[CONF_PATH] == DEFAULT_CONFIG.encode()
    assert host.directory_stats[CONF_DIR]["mode"] == 2770
    assert not host.login_path_visible(CONF_PATH)

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert HANDLER_NAME not in ubuntu_names(second)


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
    assert host.remote_files == {}
    assert host.directory_stats == {}
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
    host.remote_files[CONF_PATH] = DEFAULT_CONFIG.encode()
    host.existing_files.add(CONF_PATH)
    host.file_stats[CONF_PATH] = {
        "user": "redis",
        "group": "redis",
        "mode": 600,
        "size": len(DEFAULT_CONFIG.encode()),
    }

    report = await run_role(inventory, hosts)
    log = ubuntu_log(report)

    assert log["Configure Redis"].changed is False
    assert log["Set Redis configuration attributes"].changed
    assert log[HANDLER_NAME].changed
    assert host.file_stats[CONF_PATH]["mode"] == 640

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert HANDLER_NAME not in ubuntu_names(second)


async def test_content_drift_reinstalls_and_notifies():
    """Stale content: the install request rewrites the file and queues the restart."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    host.remote_files[CONF_PATH] = b"# stale config\n"
    host.existing_files.add(CONF_PATH)
    host.file_stats[CONF_PATH] = {
        "user": "redis",
        "group": "redis",
        "mode": 640,
        "size": len(b"# stale config\n"),
    }

    report = await run_role(inventory, hosts)
    log = ubuntu_log(report)

    assert log["Configure Redis"].changed
    assert log[HANDLER_NAME].changed
    assert host.remote_files[CONF_PATH] == DEFAULT_CONFIG.encode()

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_directory_drift_converges_without_notifying():
    """The config dir changed but carries no ``notify`` in the role: no restart.

    A ``02750`` drift on the directory is repaired by ``files.directory``, yet
    only the two config requests queue ``restart redis`` -- the wave's changed
    entry must not reach the handler.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    # Converged config, drifted directory mode.
    host.remote_files[CONF_PATH] = DEFAULT_CONFIG.encode()
    host.existing_files.add(CONF_PATH)
    host.file_stats[CONF_PATH] = {
        "user": "redis",
        "group": "redis",
        "mode": 640,
        "size": len(DEFAULT_CONFIG.encode()),
    }
    host.directory_stats[CONF_DIR] = {
        "mode": 2750,
        "user": "redis",
        "group": "redis",
    }

    report = await run_role(inventory, hosts)
    log = ubuntu_log(report)

    assert log["Ensure Redis configuration directory"].changed
    assert host.directory_stats[CONF_DIR]["mode"] == 2770
    assert HANDLER_NAME not in ubuntu_names(report)

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the override branches ------------------------------------------------------


async def test_port_and_save_overrides_ride_the_inventory():
    """The example's override pair: ``port 6380`` and no ``save`` lines."""
    inventory = make_inventory(port=6380, save=[])
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    await run_role(inventory, hosts)

    expected = DEFAULT_CONFIG.replace("port 6379\n", "port 6380\n").replace(
        "save 900 1\nsave 300 10\nsave 60 10000\n", ""
    )
    assert host.remote_files[CONF_PATH] == expected.encode()

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_enabled_false_leaves_the_unit_disabled_but_running():
    """``redis_enabled: false`` maps to ``enabled=False`` on the service task."""
    inventory = make_inventory(enabled=False)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    await run_role(inventory, hosts)

    assert host.services["redis-server.service"] == "active"
    assert "redis-server.service" not in host.enabled_services

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the notification channel ---------------------------------------------------


def test_only_the_config_requests_notify():
    """The directory, package and service names are deliberately not listeners.

    The role's one ``notify:`` sits on the template task; the companion joins it
    for metadata drift (D3). Nothing else in :data:`NOTIFY_RESTART`.
    """
    assert NOTIFY_RESTART == frozenset(
        {
            "Configure Redis",
            "Set Redis configuration attributes",
        }
    )
    assert "Ensure Redis configuration directory" not in NOTIFY_RESTART
    assert "Install Redis" not in NOTIFY_RESTART
    assert "Ensure Redis is started and enabled" not in NOTIFY_RESTART


def test_frozen_constants_are_the_debian_vars_table():
    """``vars/Debian.yml`` collapsed to constants: package, unit, paths, modes."""
    assert PACKAGE_DEFAULT == "redis-server"
    assert SERVICE == "redis-server"
    assert CONF_PATH == "/etc/redis/redis.conf"
    assert CONF_DIR == "/etc/redis"
    assert CONF_DIR_MODE == "02770"
    assert CONF_MODE == "0640"
    # D6: the packaged unit's User=/Group=, not root -- the daemon must be able
    # to read the 0640 config, and dpkg owns /etc/redis redis:redis anyway.
    assert CONF_USER == "redis"
    assert CONF_GROUP == "redis"


# --- the pure helpers, unit-tested directly --------------------------------------


def test_render_matches_the_template_defaults():
    """The Jinja2 render is the template source with every default filled."""
    rendered = render_redis_conf(RedisVars())
    assert rendered == DEFAULT_CONFIG
    assert rendered.endswith("no-appendfsync-on-rewrite no\n\n\n\n\n\n")


def test_render_conditional_branches():
    """Each ``{% if %}`` guard and ``{% for %}`` loop fills its branch exactly."""
    socketed = render_redis_conf(RedisVars(unixsocket="/var/run/redis/redis.sock"))
    assert "unixsocket /var/run/redis/redis.sock\n" in socketed

    capped = render_redis_conf(RedisVars(maxmemory=134217728))
    assert (
        "# maxclients 128\n\nmaxmemory 134217728\n"
        "maxmemory-policy noeviction\nmaxmemory-samples 5\n\nappendonly no\n"
    ) in capped

    keyed = render_redis_conf(RedisVars(requirepass="s3cret"))
    assert "requirepass s3cret\n" in keyed

    renamed = render_redis_conf(RedisVars(disabled_commands=("FLUSHDB", "KEYS")))
    assert 'rename-command FLUSHDB ""\nrename-command KEYS ""\n' in renamed

    extra = render_redis_conf(RedisVars(extra_config="# local\nprotected-mode no\n"))
    # The trailing ``{{ redis_extra_config }}`` line keeps its own newline, so a
    # value that already ends in one renders a blank final line.
    assert extra.endswith("no-appendfsync-on-rewrite no\n\n\n\n\n# local\nprotected-mode no\n\n")


def test_render_save_loop():
    """The ``save`` for-loop: the default three rules, a custom list, or none."""
    listed = render_redis_conf(RedisVars(save=("3600 1", "60 100")))
    assert listed == DEFAULT_CONFIG.replace(
        "save 900 1\nsave 300 10\nsave 60 10000\n", "save 3600 1\nsave 60 100\n"
    )

    empty = render_redis_conf(RedisVars(save=()))
    assert empty == DEFAULT_CONFIG.replace(
        "save 900 1\nsave 300 10\nsave 60 10000\n", ""
    )
    assert "\nsave " not in empty


def test_redis_plan_freezes_the_render_and_vars():
    """The plan is the rendered string plus the two read variables, hashable."""
    plan = redis_plan(RedisVars(port=6380, package="redis", enabled=False))
    assert isinstance(plan, RedisPlan)
    assert plan.config_content == render_redis_conf(RedisVars(port=6380))
    assert plan.package == "redis"
    assert plan.enabled is False
    assert hash(plan) == hash(
        RedisPlan(config_content=plan.config_content, package="redis", enabled=False)
    )


def test_model_is_frozen_and_hashable():
    v = RedisVars()
    assert hash(v) is not None
    with pytest.raises(ValidationError):
        v.port = 6380


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"port": "not-a-number"}, "port"),
        ({"loglevel": "trace"}, "loglevel"),
        ({"appendonly": "true"}, "appendonly"),
        ({"appendfsync": "sometimes"}, "appendfsync"),
        ({"rdbcompression": "1"}, "rdbcompression"),
        ({"package": ""}, "package"),
        ({"dbdir": ""}, "dbdir"),
    ],
)
def test_invalid_role_values_fail_fast(overrides, match):
    """The role's implicit type gates are pydantic rejections now (D5)."""
    inventory = make_inventory(**overrides)
    with pytest.raises(ValidationError, match=match):
        resolve_vars(RedisVars, "redis", inventory)


def test_numeric_strings_coerce_into_the_integer_fields():
    """The one widening over strict types: pydantic's lax int coercion (D5).

    ``"6380"`` binds to ``port`` as ``6380`` (the repo-wide convention, as in
    ``MemcachedVars.port``) and renders identically, so the fail-fast claim
    covers *unreadable* values, not merely differently-typed ones.
    """
    v = resolve_vars(RedisVars, "redis", make_inventory(port="6380"))
    assert v["ubuntu"].port == 6380
    assert "port 6380\n" in render_redis_conf(v["ubuntu"])


def test_inventory_role_variables_bind_and_typos_raise():
    """The nested mapping binds bare names; a typo raises RoleVarsError."""
    inventory = make_inventory(
        save=["3600 1"], includes=["/etc/redis/local.conf"], enabled=False
    )
    v = resolve_vars(RedisVars, "redis", inventory)
    # A JSON list arrives normalized to a hashable tuple (§8.1).
    assert v["ubuntu"].save == ("3600 1",)
    assert v["ubuntu"].includes == ("/etc/redis/local.conf",)
    assert v["ubuntu"].enabled is False
    # The RHEL-only knob still binds (it is inert, not unknown).
    remi = resolve_vars(RedisVars, "redis", make_inventory(enablerepo="remi"))
    assert remi["ubuntu"].enablerepo == "remi"
    assert RedisVars(enablerepo="remi").enablerepo == "remi"

    with pytest.raises(RoleVarsError, match="packagez"):
        resolve_vars(RedisVars, "redis", make_inventory(packagez="x"))
