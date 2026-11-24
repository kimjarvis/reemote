# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``php`` role (design doc section 17.7, T6).

The role is library code now, so it is imported (``from reemote.roles import
php``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, php(v), host_factory=...)``. The old example's harness --
import the script by path, ``gather_fact`` between four ``run()`` calls, freeze
four ``plans`` dicts keyed by host name -- is gone: the barriers inside the body
are the snapshot points and the driver runs them, so a test only runs the role.

Three assertions carry this migration's claims, and they are the ones design doc
section 16.6 says the role exists to prove:

* **three checkpoints, unioned** -- the webserver notification set is folded out
  of the install, cleanup *and* configure waves. A test changes something in each
  wave alone and the handler still fires, which no single-report version of the
  body can do: ``apply_report`` is per wave, so a checkpoint that does not follow
  its own stage reads an empty report and the notification is lost silently.
* **the gate** -- ``package_changed`` comes from the install wave only, so the
  packaged-ini cleanup runs exactly on a host whose install changed.
* **convergence** -- rerun against the same persistent hosts and the second pass
  reports no change anywhere and queues no handler request.

Seeding choices, none of them a bridge:

* the conf tree and the packaged extension ini files are seeded as the
  *post-install* state of ``php8.3-fpm``, because the simulation's apt install
  records package names only. The packaged ``conf.d`` entries are a mix of
  symlinks (stock Ubuntu layout, which is why the removals ask ``force=True``)
  and regular files, so both removal branches get exercised.
* ``grep_files`` is seeded per ``(path, pattern)`` rather than derived from
  ``remote_files``: the seam is question-keyed and no mutator refreshes it, so
  the second pass re-plans the same stray removals and they report no change.
  Nothing is re-derived between passes -- the barriers are re-read, which is the
  point of making them barriers.
* the FPM pool directory is seeded as *present* (as ``php8.3-fpm`` ships it) in
  both the ``Directory`` and ``test -e`` stores, because ``make_directory``
  maintains only the former: seeding the gate answer rather than creating the
  directory avoids a bridge, and the absent-directory branch has its own test.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from reemote import run_waves
from reemote.handlers import changed_hosts
from reemote.roles import PhpOutputs, PhpVars, php
from reemote.roles.php import (
    ANSIBLE_MANAGED,
    APCU_PATTERN,
    CLEANUP_HANDLER_NAMES,
    CONFIGURE_HANDLER_NAMES,
    DEBIAN_DEFAULT_PHP_VERSION,
    FPM_HANDLER_NAMES,
    INSTALL_HANDLER_NAMES,
    OPCACHE_PATTERN,
    PACKAGE_GATE_NAMES,
    UBUNTU_PHP_VERSIONS,
    WEBSERVER_HANDLER_NAMES,
    PhpFpmPool,
    _ansible_bool,
    configure_plan,
    default_pools,
    install_plan,
    opcache_enabled,
    packaged_cleanup_plan,
    php_version,
    render_apc_ini,
    render_opcache_ini,
    render_php_ini,
    render_www_conf,
    resolve_php,
    stray_configs,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from reemote.stages import scoped
from tests.simulated_host import SimulatedHost

# Ubuntu 24.04 -> the vars/Ubuntu-24.yml PHP version (8.3). Every path below is
# the vars/Debian.yml __php_* shape resolved for that version.
VERSION = "8.3"
C_FPM = f"/etc/php/{VERSION}/fpm"
C_APACHE = f"/etc/php/{VERSION}/apache2"
C_CLI = f"/etc/php/{VERSION}/cli"
E_FPM = f"{C_FPM}/conf.d"
E_APACHE = f"{C_APACHE}/conf.d"
E_CLI = f"{C_CLI}/conf.d"
CONF_PATHS = (C_FPM, C_APACHE, C_CLI)
EXT_PATHS = (E_FPM, E_APACHE, E_CLI)
POOL_DIR = f"{C_FPM}/pool.d"
POOL_FILE = f"{POOL_DIR}/www.conf"
MODS = f"/etc/php/{VERSION}/mods-available"
APC_FILE = "20-apcu.ini"
OPCACHE_FILE = "10-opcache.ini"
PACKAGED_APCU = f"{E_FPM}/{APC_FILE}"
PACKAGED_OPCACHE = f"{E_APACHE}/{OPCACHE_FILE}"

# The pre-state stray configs the FindInFiles sweep must classify: two removals
# (a direct, non-hidden hit and one grep matched mid-line) and two keeps (a
# dotfile, a nested file).
STRAY_APCU = f"{E_CLI}/stray-apcu.ini"
HIDDEN_APCU = f"{E_CLI}/.hidden-apcu.ini"
NESTED_APCU = f"{E_CLI}/nested/old-apcu.ini"
COMMENTED_APCU = f"{E_CLI}/zz-commented.ini"
STRAY_OPCACHE = f"{E_APACHE}/stray-opcache.ini"

INSTALL_NAMES = ("Update apt cache", "Ensure PHP packages are installed")
CLEANUP_NAMES = (
    "Delete APCu configuration file if this role will provide one",
    "Delete OpCache configuration file if this role will provide one",
)
#: The configure wave of the base inventory: six directories (all already
#: present), the managed php.ini in each conf path, the two stray removals the
#: sweep found, the role's own APCu/OpCache ini in each extension path, the pool
#: file and the (non-notifying) FPM service task.
CONFIGURE_NAMES = (
    "Ensure configuration directories exist",
    "Place PHP configuration file in place",
    "Remove any non-role-supplied APCu config files",
    "Remove any non-role-supplied OpCache config files",
    "Ensure APCu config file is present",
    "Ensure OpCache config file is present",
    "Create fpm pools",
    "Ensure php-fpm is started and enabled at boot (if configured)",
)
APACHE_HANDLER = "Restart apache2 (handler)"
FPM_HANDLER = f"Restart php{VERSION}-fpm (handler)"
POOL_SERVICE = "Ensure php-fpm is started and enabled at boot (if configured)"
POOL_DIR_REQUEST = "Ensure the default pool directory exists"

#: The barrier sequence of the base inventory: the distribution read, the install
#: and cleanup checkpoints, six content sweeps, the pool-directory stat and the
#: third checkpoint -- and no request after it unless a handler fires.
BASE_BARRIERS = [
    "FactRequest",  # LinuxDistribution
    "CheckpointRequest",  # install done
    "CheckpointRequest",  # cleanup done
    "FactRequest",  # FindInFiles, once per extension path and pattern
    "FactRequest",
    "FactRequest",
    "FactRequest",
    "FactRequest",
    "FactRequest",
    "FactRequest",  # FileExists, the pool directory
    "CheckpointRequest",  # configure done
]


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride on the entry as ``php_``-prefixed extra keys, which
    is what ``resolve_vars(PhpVars, "php_", inventory)`` binds, so a test changes
    a variable exactly the way an operator does. The base entry exercises the
    non-default branches: FPM enabled (pools, service, the chained handler), a
    tuned memory limit, and both conditional template lines (the FPM status path
    and the OpCache blacklist).
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
        "php_enable_php_fpm": True,
        "php_memory_limit": "512M",
        "php_fpm_pm_status_path": "/status",
        "php_opcache_blacklist_filename": "/etc/php/opcache-blacklist",
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


def _regular_file(host: SimulatedHost, path: str, content: bytes) -> None:
    """Seed a regular file across the three stores its facts read."""
    host.remote_files[path] = content
    host.existing_files.add(path)
    host.file_stats[path] = {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(content),
    }


def _symlink(host: SimulatedHost, path: str, target: str) -> None:
    """Seed a symlink: ``links``/``link_stats`` plus a ``False`` File-fact row.

    The ``File`` fact reads ``file_stats`` only, so ``False`` is what makes
    ``files.file(present=False, force=True)`` take the remove-the-link branch --
    which is why the packaged ``conf.d`` entries need ``force`` at all.
    """
    host.links[path] = target
    host.link_stats[path] = {
        "mode": 777,
        "user": "root",
        "group": "root",
        "link_target": target,
    }
    host.file_stats[path] = False


def _grep(host: SimulatedHost) -> None:
    """Seed the ``FindInFiles`` answers for the role's two sweeps.

    Question-keyed on ``(path, pattern)``, as the seam is read. Only regular
    files answer: ``grep -r`` skips the symlinks in a packaged ``conf.d``, which
    is part of why the two searches agree on a stock machine.
    """
    host.grep_files = {
        (E_FPM, APCU_PATTERN): [PACKAGED_APCU],
        (E_APACHE, APCU_PATTERN): [],
        (E_CLI, APCU_PATTERN): sorted(
            [STRAY_APCU, HIDDEN_APCU, NESTED_APCU, COMMENTED_APCU]
        ),
        (E_FPM, OPCACHE_PATTERN): [],
        (E_APACHE, OPCACHE_PATTERN): sorted([STRAY_OPCACHE, PACKAGED_OPCACHE]),
        (E_CLI, OPCACHE_PATTERN): [],
    }


def seed(host: SimulatedHost) -> None:
    """Pre-state: a stock Ubuntu 24.04 machine with PHP not yet installed.

    See the module docstring: the package's own files are seeded as its
    post-install state, because the simulation installs names, not payloads.
    """
    host.linux_distribution = {
        "name": "Ubuntu",
        "family": "Debian",
        "major": 24,
        "minor": 0,
        "release_meta": {},
    }
    host.apt_packages = {}
    host.apt_update_stamp = None
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}
    host.links = {}
    host.link_stats = {}
    host.directory_stats = {
        path: {"mode": 755, "user": "root", "group": "root"}
        for path in (*CONF_PATHS, *EXT_PATHS, POOL_DIR, MODS, f"{E_CLI}/nested")
    }
    # FileExists is ``test -e``: the seeded directories answer it too, so the
    # pool-dir gate reads True exactly as it does on a machine php8.3-fpm left.
    host.existing_files |= set(host.directory_stats)
    host.services = {}
    host.enabled_services = set()

    # The packaged extension ini files: a mix of symlinks (the stock Ubuntu
    # conf.d layout) and regular files, so the cleanup exercises both the
    # force/remove-tree branch and the plain rm -f branch. Their targets live
    # in mods-available, outside the searched conf.d trees.
    _regular_file(host, f"{MODS}/apcu.ini", b"extension=apcu.so\n")
    _regular_file(host, f"{MODS}/opcache.ini", b"zend_extension=opcache.so\n")
    _symlink(host, f"{E_CLI}/{APC_FILE}", f"{MODS}/apcu.ini")
    _symlink(host, f"{E_APACHE}/{APC_FILE}", f"{MODS}/apcu.ini")
    _regular_file(host, PACKAGED_APCU, b"extension=apcu.so\n; packaged\n")
    _symlink(host, f"{E_FPM}/{OPCACHE_FILE}", f"{MODS}/opcache.ini")
    _symlink(host, f"{E_CLI}/{OPCACHE_FILE}", f"{MODS}/opcache.ini")
    _regular_file(host, PACKAGED_OPCACHE, b"zend_extension=opcache.so\n; packaged\n")
    _regular_file(host, POOL_FILE, b"[www]\nlisten = 127.0.0.1:9000\npm = dynamic\n")

    # Stray configs the sweep must remove (direct, non-hidden) and the ones it
    # must keep (a dotfile, a nested file). The commented line matches only
    # because grep matches anywhere in a line -- the documented deviation from
    # find's line-start-anchored ``contains``.
    _regular_file(host, STRAY_APCU, b"extension=apcu.so\n")
    _regular_file(host, HIDDEN_APCU, b"extension=apcu.so\n")
    _regular_file(host, NESTED_APCU, b"extension=apcu.so\n")
    _regular_file(host, COMMENTED_APCU, b"; extension=apcu.so (disabled)\n")
    _regular_file(host, STRAY_OPCACHE, b"zend_extension=opcache.so\n")

    _grep(host)


def _drop_package(host: SimulatedHost, name: str = f"php{VERSION}-gd") -> None:
    """Drift one installed package away, so the install wave reports a change.

    This is the only lever on the cleanup wave: its deploy is gated on that
    wave's report, so nothing in it runs on a converged host.
    """
    del host.apt_packages[name]


def _reinstall_packaged_ini(host: SimulatedHost) -> None:
    """Put the packaged extension ini files back, exactly as ``apt`` leaves them.

    The simulation installs package names, not payloads, so a test that wants the
    cleanup wave to have something to delete has to restore them by hand -- that is
    what an ``apt install --reinstall`` does on a real machine.
    """
    _symlink(host, f"{E_CLI}/{APC_FILE}", f"{MODS}/apcu.ini")
    _symlink(host, f"{E_APACHE}/{APC_FILE}", f"{MODS}/apcu.ini")
    _symlink(host, f"{E_FPM}/{OPCACHE_FILE}", f"{MODS}/opcache.ini")
    _symlink(host, f"{E_CLI}/{OPCACHE_FILE}", f"{MODS}/opcache.ini")
    _regular_file(host, PACKAGED_APCU, b"extension=apcu.so\n; packaged\n")
    _regular_file(host, PACKAGED_OPCACHE, b"zend_extension=opcache.so\n; packaged\n")


def _clear_extension_ini(host: SimulatedHost) -> None:
    """Take every extension conf.d ini path out of every store the facts read.

    Used where a test needs a wave to be quiet: the cleanup wave has nothing to
    delete and the configure wave's installs start from absent, so only the path
    the test drifted can report a change.
    """
    for ext in EXT_PATHS:
        for filename in (APC_FILE, OPCACHE_FILE):
            path = f"{ext}/{filename}"
            host.remote_files.pop(path, None)
            host.existing_files.discard(path)
            host.file_stats.pop(path, None)
            host.links.pop(path, None)
            host.link_stats.pop(path, None)


def make_hosts(inventory: list[dict[str, Any]]) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded target.

    Only the Ubuntu entry is seeded, so the RedHat host keeps its stock empty
    stores and an accidental request on it shows up as untouched state.
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
    v = resolve_vars(PhpVars, "php_", inventory)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    report, _hosts = await run_waves(
        inventory, php(v), host_factory=factory, dry=dry
    )
    return report


async def run_and_capture(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SimulatedHost],
) -> tuple[Any, dict[str, PhpOutputs]]:
    """Run the role inside a scope and keep what each host's body returned.

    The outputs are the §9/§16.8 channel to a composing role, and the only way to
    read ``webserver_notified`` back: the parent here is the shape §10.4 shows at
    a composition site, ``scoped`` label included.
    """
    v = resolve_vars(PhpVars, "php_", inventory)
    captured: dict[str, PhpOutputs] = {}

    def parent(host):
        captured[host.name] = yield from scoped("php", php(v))(host)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    report, _hosts = await run_waves(inventory, parent, host_factory=factory)
    return report, captured


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


def entries_named(report: Any, name: str) -> list[Any]:
    """Every log entry with *name*, on any host."""
    return [entry for entry in report.merged().log if entry.name == name]


def host_records(report: Any, host: str = "ubuntu") -> list[Any]:
    """Every record (request step or barrier) attributed to *host*."""
    return [record for record in report.records if record.host == host]


def barriers(report: Any, host: str = "ubuntu") -> list[Any]:
    """The barrier objects *host* parked on, in wave order."""
    return [record.barrier for record in host_records(report, host) if record.barrier]


def checkpoint_records(report: Any, host: str = "ubuntu") -> list[Any]:
    """The three checkpoint records of one pass, in order."""
    return [
        record
        for record in host_records(report, host)
        if record.barrier is not None
        and type(record.barrier).__name__ == "CheckpointRequest"
    ]


def waves(report: Any, host: str = "ubuntu") -> list[int]:
    """The distinct wave numbers *host* emitted records in."""
    return sorted({record.wave for record in host_records(report, host)})


def wave_of(report: Any, name: str) -> int:
    """The wave one request name was applied in."""
    return next(
        record.wave
        for record in report.records
        if record.planned is not None and record.planned.request.name == name
    )


def request_kwargs(report: Any, name: str) -> dict:
    """The forwarded keyword arguments of the one planned request called *name*."""
    record = next(
        record
        for record in report.records
        if record.planned is not None and record.planned.request.name == name
    )
    return record.planned.request.kwargs


def text(host: SimulatedHost, path: str) -> str:
    """What a simulated file holds, decoded."""
    return host.remote_files[path].decode("utf-8")


def handler_names(report: Any) -> list[str]:
    """The handler entries of one pass, in the order they ran."""
    return [
        entry.name
        for entry in report.merged().log
        if entry.host == "ubuntu" and entry.name.endswith("(handler)")
    ]


# --- the first pass really deploys the role -----------------------------------


async def test_first_pass_installs_and_configures_php():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # The packages are installed and the cache refresh ran.
    assert "php8.3-cli" in host.apt_packages
    assert "php8.3-fpm" in host.apt_packages
    assert "php8.3-apcu" in host.apt_packages
    assert host.apt_update_stamp is not None

    # The packaged extension ini files went through the cleanup wave: the stock
    # symlinks are gone, and where the package had shipped a regular file the
    # role's own content replaced it (asserted below) -- a delete plus an install,
    # both in this role's two waves.
    assert f"{E_CLI}/{APC_FILE}" not in host.links
    assert f"{E_APACHE}/{APC_FILE}" not in host.links
    assert f"{E_FPM}/{OPCACHE_FILE}" not in host.links
    assert f"{E_CLI}/{OPCACHE_FILE}" not in host.links
    assert text(host, PACKAGED_APCU).startswith("extension=apcu.so\napc.shm_size=")
    assert "; packaged" not in text(host, PACKAGED_OPCACHE)

    # The role's own ini files replaced them, in every extension conf path.
    for ext in EXT_PATHS:
        assert text(host, f"{ext}/{APC_FILE}") == (
            "extension=apcu.so\napc.shm_size=96M\napc.enable_cli=0\napc.rfc1867=1\n"
        )
        opcache = text(host, f"{ext}/{OPCACHE_FILE}")
        assert opcache.startswith("zend_extension=opcache.so\nopcache.enable=1\n")
        assert "opcache.blacklist_filename=/etc/php/opcache-blacklist\n" in opcache
        assert host.file_stats[f"{ext}/{OPCACHE_FILE}"]["mode"] == 644

    # The managed php.ini in every conf path, with the inventory's override and
    # the role's defaults around it.
    for path in CONF_PATHS:
        ini = text(host, f"{path}/php.ini")
        assert ini.startswith("[PHP]\n")
        assert "memory_limit = 512M\n" in ini
        assert "max_execution_time = 60\n" in ini
        assert "date.timezone = America/Chicago\n" in ini
        assert "session.name = PHPSESSID\n" in ini
        assert ini.endswith("ldap.max_links = -1\n")
        assert host.file_stats[f"{path}/php.ini"]["user"] == "root"

    # The stray sweep: direct, non-hidden hits removed; the dotfile and the
    # nested file kept; the role's own filename never a target.
    assert STRAY_APCU not in host.remote_files
    assert COMMENTED_APCU not in host.remote_files
    assert HIDDEN_APCU in host.remote_files
    assert NESTED_APCU in host.remote_files
    assert STRAY_OPCACHE not in host.remote_files

    # The FPM pool, rendered from the bundled template with the role's gate
    # values: the falsy ``pm_max_requests`` becomes 500, the status path appears.
    pool = text(host, POOL_FILE)
    assert pool.startswith("; Ansible managed\n\n[www]\n")
    assert "listen = 127.0.0.1:9000\n" in pool
    assert "user = www-data\ngroup = www-data\n" in pool
    assert "listen.owner = www-data\nlisten.group = www-data\n" in pool
    assert "pm = dynamic\n" in pool
    assert "pm.max_requests = 500\n" in pool
    assert "pm.status_path = /status\n" in pool
    assert host.file_stats[POOL_FILE]["mode"] == 644

    # The FPM service, started and enabled at boot.
    assert host.services[f"php{VERSION}-fpm.service"] == "active"
    assert f"php{VERSION}-fpm.service" in host.enabled_services

    # Both handlers ran, webserver first -- the notification chain's order.
    assert handler_names(report) == [APACHE_HANDLER, FPM_HANDLER]
    assert entry_named(report, APACHE_HANDLER).changed
    assert entry_named(report, FPM_HANDLER).changed


async def test_one_stream_replaces_the_four_deploy_split():
    """Install, cleanup, sweeps, configure, checkpoints and handler share one pass.

    The old example needed four ``run()`` calls and three Python-side snapshots
    between them. Here the barriers *are* those snapshots, so the whole role is
    one pass -- eleven waves for the base inventory, twelve once the handler runs.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert [type(b).__name__ for b in barriers(report)] == BASE_BARRIERS
    assert waves(report) == list(range(12))

    ordered = [entry.name for entry in report.merged().log if entry.host == "ubuntu"]
    assert ordered[:2] == list(INSTALL_NAMES)
    # Three extension paths per delete task, in the task's own order.
    assert ordered[2:8] == [CLEANUP_NAMES[0]] * 3 + [CLEANUP_NAMES[1]] * 3
    assert ordered[-2:] == [APACHE_HANDLER, FPM_HANDLER]


async def test_each_checkpoint_closes_the_stage_it_reports_on():
    """The §16.6 placement rule, proved structurally rather than by behaviour.

    Every checkpoint sits in the same wave as the requests it must read, and no
    two share a wave. That is what keeps the three channels apart: a checkpoint
    that trails a combined wave sees an empty report and loses its
    notifications without ever saying so.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    records = checkpoint_records(report)
    assert [record.barrier.name for record in records] == [
        "install done",
        "cleanup done",
        "configure done",
    ]
    assert [record.wave for record in records] == [1, 2, 10]
    # The stage and its checkpoint share a wave, and no wave holds two.
    assert wave_of(report, "Ensure PHP packages are installed") == 1
    assert wave_of(report, CLEANUP_NAMES[0]) == 2
    assert wave_of(report, "Create fpm pools") == 10
    # The six content sweeps and the pool stat each cost their own wave.
    sweep_waves = [
        record.wave
        for record in host_records(report)
        if record.barrier is not None
        and type(record.barrier).__name__ == "FactRequest"
    ]
    assert sweep_waves == [0, 3, 4, 5, 6, 7, 8, 9]


# --- the three notification channels (design doc section 16.6) ----------------


async def test_the_three_checkpoints_read_three_different_reports():
    """Each checkpoint sees its own wave and nothing else -- the §16.6 rule.

    Drift a package and put the packaged ini files back: the install wave
    installs, the cleanup wave deletes and the configure wave re-writes. Three
    changed waves, three reports, each holding exactly its own stage. That is what
    makes the union meaningful -- three checkpoints stacked after one combined wave
    would leave the last two reading an empty report, and the handler would
    silently never run.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    host = hosts["ubuntu"]
    _drop_package(host)
    _reinstall_packaged_ini(host)

    report = await run_role(inventory, hosts)
    install_rep, cleanup_rep, configure_rep = (
        record.outcome for record in checkpoint_records(report)
    )

    assert changed_hosts(install_rep, PACKAGE_GATE_NAMES) == frozenset({"ubuntu"})
    assert changed_hosts(cleanup_rep, CLEANUP_HANDLER_NAMES) == frozenset({"ubuntu"})
    assert changed_hosts(configure_rep, CONFIGURE_HANDLER_NAMES) == frozenset({"ubuntu"})

    # Each report is blind to the other stages, because each closed its own wave.
    assert changed_hosts(install_rep, CLEANUP_HANDLER_NAMES) == frozenset()
    assert changed_hosts(cleanup_rep, PACKAGE_GATE_NAMES) == frozenset()
    assert changed_hosts(configure_rep, CLEANUP_HANDLER_NAMES) == frozenset()

    assert "install done → 1 host notified" in report.tree()
    assert "cleanup done → 1 host notified" in report.tree()
    assert "configure done → 1 host notified" in report.tree()
    assert handler_names(report) == [APACHE_HANDLER, FPM_HANDLER]


async def test_install_only_change_still_notifies():
    """One changed wave is enough: the first checkpoint carries the relay alone.

    Both extensions are switched off, so the cleanup wave has nothing to delete
    and the configure wave -- the last report folded in -- changes nothing. A body
    that read only the final checkpoint would restart nothing on this pass.
    """
    inventory = make_inventory(php_enable_apc=False, php_opcache_enable="0")
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    _drop_package(hosts["ubuntu"])
    report = await run_role(inventory, hosts)

    install = entry_named(report, "Ensure PHP packages are installed")
    assert install.changed
    outside = [e.name for e in changed_entries(report) if not e.name.endswith("(handler)")]
    assert outside == [install.name]
    assert [e for e in report.merged().log if e.name in CLEANUP_NAMES] == []
    assert "install done → 1 host notified" in report.tree()
    assert "cleanup done → 0 hosts notified" in report.tree()
    assert "configure done → 0 hosts notified" in report.tree()
    assert handler_names(report) == [APACHE_HANDLER, FPM_HANDLER]


async def test_configure_only_change_still_notifies():
    """The other half: a drifted ``php.ini`` reaches the handler through wave ten.

    Nothing installed changed, so the cleanup wave is gated shut and its report is
    empty; only the third checkpoint sees this one. It is the notification the old
    example's install-report-only relay would have dropped.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    host = hosts["ubuntu"]
    host.remote_files[f"{C_CLI}/php.ini"] = b"[PHP]\nmemory_limit = 128M\n"

    report = await run_role(inventory, hosts)

    assert not entry_named(report, "Ensure PHP packages are installed").changed
    assert [e for e in report.merged().log if e.name in CLEANUP_NAMES] == []
    assert "install done → 0 hosts notified" in report.tree()
    assert "cleanup done → 0 hosts notified" in report.tree()
    assert "configure done → 1 host notified" in report.tree()
    ini_entries = entries_named(report, "Place PHP configuration file in place")
    assert [e.changed for e in ini_entries] == [False, False, True]
    assert handler_names(report) == [APACHE_HANDLER, FPM_HANDLER]
    assert text(host, f"{C_CLI}/php.ini").startswith("[PHP]\n\n;;;")


async def test_the_relay_is_the_union_and_not_the_last_wave():
    """``PhpOutputs.webserver_notified`` folds all three reports together.

    The configure wave is quiet here, so the last checkpoint alone would publish
    nobody: the set apache consumes (§16.8) has to be the union, and it is.
    """
    inventory = make_inventory(php_enable_apc=False, php_opcache_enable="0")
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    _drop_package(hosts["ubuntu"])
    report, captured = await run_and_capture(inventory, hosts)
    outputs = captured["ubuntu"]

    assert outputs.webserver_notified == frozenset({"ubuntu"})
    assert outputs.version == VERSION
    assert outputs.extension_conf_paths == EXT_PATHS
    # Not the last report's set: that one is empty, and so is the middle one.
    install_rep, cleanup_rep, configure_rep = (
        record.outcome for record in checkpoint_records(report)
    )
    assert changed_hosts(configure_rep, CONFIGURE_HANDLER_NAMES) == frozenset()
    assert changed_hosts(cleanup_rep, CLEANUP_HANDLER_NAMES) == frozenset()
    assert changed_hosts(install_rep, INSTALL_HANDLER_NAMES) == frozenset({"ubuntu"})


async def test_pool_change_alone_queues_only_the_fpm_handler():
    """``fpm_direct`` reads the configure wave, and the chain runs the other way.

    A pool whose content drifted is in :data:`FPM_HANDLER_NAMES` only, so this
    restarts php-fpm without touching the webserver -- the notification that
    ``restart webserver`` would otherwise have to arrive through the chain.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    host = hosts["ubuntu"]
    host.remote_files[POOL_FILE] = b"[www]\nlisten = 127.0.0.1:9000\npm = static\n"

    report = await run_role(inventory, hosts)

    assert entry_named(report, "Create fpm pools").changed
    assert "configure done → 1 host notified" in report.tree()
    assert handler_names(report) == [FPM_HANDLER]
    assert not any(e.name.startswith("Restart apache2") for e in report.merged().log)
    assert "Restart apache2" not in report.tree()


# --- the package-change gate --------------------------------------------------


async def test_packaged_cleanup_runs_only_on_a_changed_install():
    """``register`` + ``when`` as a per-host gate on the install wave's report.

    A converged host has nothing to delete -- its packaged ini files went away on
    the pass that installed them -- so the cleanup wave holds no request at all.
    The wave is empty by *gate*, not by idempotence, which is why the checkpoint
    right after it reports nobody.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert entries_named(first, CLEANUP_NAMES[0])

    host = hosts["ubuntu"]
    # The packaged files are back on disk: only the install gate keeps the
    # cleanup wave from deleting them again.
    _reinstall_packaged_ini(host)
    second = await run_role(inventory, hosts)

    assert entries_named(second, CLEANUP_NAMES[0]) == []
    assert entries_named(second, CLEANUP_NAMES[1]) == []
    assert "cleanup done → 0 hosts notified" in second.tree()
    # The checkpoint still sits in its own wave, gate or no gate.
    assert [record.wave for record in checkpoint_records(second)] == [1, 2, 10]


async def test_disabled_extension_is_not_deleted_on_install():
    """The enable gate lives in the plan, the changed gate in the deploy.

    With ``php_enable_apc: false`` the cleanup wave has no APCu removal to make
    -- the role only deletes a packaged ini it intends to replace.
    """
    inventory = make_inventory(php_enable_apc=False)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert [e for e in report.merged().log if e.name == CLEANUP_NAMES[0]] == []
    assert entries_named(report, CLEANUP_NAMES[1])
    # APC disabled instead means the role removes its own ini path everywhere.
    removal = [
        e.name
        for e in report.merged().log
        if e.name == "Remove APCu config file if APC is disabled"
    ]
    assert len(removal) == len(EXT_PATHS)


# --- the family guard ---------------------------------------------------------


async def test_non_ubuntu_host_emits_nothing():
    """The family guard is the body's opening ``if``, not a ``when`` on each op.

    A host outside the ``"Ubuntu"`` family returns before the first barrier, so
    it records no request and no barrier -- where the old example logged each of
    its requests on that host as ``[skipped: when guard ...]``.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report, captured = await run_and_capture(inventory, hosts)
    skipped = hosts["web1"]

    assert host_records(report, "web1") == []
    assert [e for e in report.merged().log if e.host == "web1"] == []
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}
    assert skipped.grep_files == {}
    assert skipped.services == {}
    assert captured["web1"] == PhpOutputs(version=None)


# --- the FPM branch -----------------------------------------------------------


async def test_fpm_disabled_drops_the_requests_but_not_the_pool_read():
    """``php_enable_php_fpm: false`` removes requests, not barriers or waves.

    The pools, the FPM service task and the chained restart disappear, but the
    pool-directory ``stat`` stays: the role guards that task with nothing at all
    (``configure-fpm.yml``'s only FPM conditions are on the pool and service
    tasks), because its answer decides whether the directory is created.
    """
    inventory = make_inventory(php_enable_php_fpm=False)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert [type(b).__name__ for b in barriers(report)] == BASE_BARRIERS
    assert waves(report) == list(range(12))
    assert "barrier: FileExists" in report.tree()
    assert entries_named(report, "Create fpm pools") == []
    assert entries_named(report, POOL_SERVICE) == []
    # The read answered True for the seeded ``pool.d``, so there is no create
    # request -- the same gate the role's stat drives.
    assert entries_named(report, POOL_DIR_REQUEST) == []
    # Only the webserver's own unit moved: no php-fpm service, no pool, no boot
    # enablement -- the whole FPM branch is off.
    assert f"php{VERSION}-fpm.service" not in hosts["ubuntu"].services
    assert hosts["ubuntu"].enabled_services == set()
    assert handler_names(report) == [APACHE_HANDLER]
    assert POOL_FILE in hosts["ubuntu"].remote_files  # the packaged file survives

    # Drop the directory and a FPM-disabled host still has it created: the role
    # gates that task on the stat alone, never on php_enable_php_fpm.
    host = hosts["ubuntu"]
    host.directory_stats.pop(POOL_DIR)
    host.existing_files.discard(POOL_DIR)
    second = await run_role(inventory, hosts)

    assert entry_named(second, POOL_DIR_REQUEST).changed
    assert entries_named(second, "Create fpm pools") == []


async def test_absent_pool_directory_is_created():
    """The one request the pool-directory barrier gates, and the gate's answer.

    ``FileExists`` is the role's own ``stat``: with the directory dropped from the
    ``test -e`` store the barrier answers ``False`` and the configure wave gains
    ``Ensure the default pool directory exists``. Seed it back and the request
    disappears -- the role only creates the directory when the read found nothing.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    host.directory_stats.pop(POOL_DIR)
    host.existing_files.discard(POOL_DIR)

    report = await run_role(inventory, hosts)

    stat = [
        record
        for record in host_records(report)
        if record.barrier is not None
        and type(record.barrier).__name__ == "FactRequest"
        and record.wave == 9
    ]
    assert stat[0].outcome is False
    assert entry_named(report, POOL_DIR_REQUEST).changed
    assert POOL_DIR in host.directory_stats

    second = await run_role(inventory, hosts)
    assert not entry_named(second, POOL_DIR_REQUEST).changed
    assert not entry_named(second, "Create fpm pools").changed


async def test_present_pool_directory_skips_the_create_request():
    """The gate's other branch: a present ``pool.d`` means no request at all."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert entries_named(report, POOL_DIR_REQUEST) == []
    assert "barrier: FileExists → True" in report.tree()


@pytest.mark.parametrize(
    ("fpm_handler_state", "expected", "reports_change"),
    [
        ("restarted", f"Restart php{VERSION}-fpm (handler)", True),
        ("reloaded", f"Reload php{VERSION}-fpm (handler)", True),
        ("started", f"Start php{VERSION}-fpm (handler)", False),
    ],
)
async def test_fpm_handler_verb_follows_the_state(
    fpm_handler_state, expected, reports_change
):
    """``php_fpm_handler_state`` selects the FPM verb, and only that verb.

    The example partitioned the notified hosts by action and composed one handler
    deploy per verb; a per-host body has exactly one state to honour.
    ``restarted``/``reloaded`` always report changed, faithful to Ansible's
    service module; ``started`` is fact-gated, and the configure wave already has
    the unit running, so it queues the verb and the verb no-ops.
    """
    inventory = make_inventory(php_fpm_handler_state=fpm_handler_state)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert entry_named(report, expected).changed is reports_change
    assert [name for name in handler_names(report) if "php8" in name] == [expected]


async def test_fpm_state_stopped_suppresses_the_fpm_handler():
    """The handler's own ``when``, not the service task's request.

    ``php_fpm_state: stopped`` leaves the FPM set empty (§16.6: the chained
    handler fires only for a started unit), while the configure wave's service
    task still asks for the unit to be stopped.
    """
    inventory = make_inventory(php_fpm_state="stopped")
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert handler_names(report) == [APACHE_HANDLER]
    service = request_kwargs(report, POOL_SERVICE)
    assert service["running"] is False
    assert service["enabled"] is True


async def test_enable_webserver_false_drops_the_chain_but_not_the_pool():
    """The webserver gate is a handler gate, not a configure one.

    Nothing in the task list is skipped by ``php_enable_webserver``: the ini
    files are still written and still notify. Only ``restart webserver`` is
    suppressed -- and the pool's own notification still reaches php-fpm.
    """
    inventory = make_inventory(php_enable_webserver=False)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert entry_named(report, "Place PHP configuration file in place").changed
    assert "configure done → 1 host notified" in report.tree()
    assert handler_names(report) == [FPM_HANDLER]


# --- convergence --------------------------------------------------------------


async def test_second_pass_changes_nothing():
    """Two passes over the same hosts: every barrier re-read, every request quiet.

    Nothing is cached between the runs -- the role converges because its requests
    are fact-gated and each stage is closed by its own checkpoint. The converged
    pass still pays for the distribution read, the six content sweeps and the
    pool stat, and it queues no handler request at all.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    second = await run_role(inventory, hosts)

    assert changed_entries(second) == []
    assert waves(second) == list(range(11))
    assert [type(b).__name__ for b in barriers(second)] == BASE_BARRIERS
    assert handler_names(second) == []
    assert second.tree() == CONVERGED_TREE


async def test_dry_preview_stops_at_the_distribution_barrier():
    """``dry`` plans up to the first barrier and refuses to guess past it.

    Everything this role does is keyed on the gathered release -- the version, the
    package list, every managed path -- so a preview that invented an answer would
    be inventing the deployment. The tree parks on ``unknown until run``, the log
    is empty and no command was ever sent (§16.6).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    report = await run_role(inventory, hosts, dry=True)

    assert report.merged().log == []
    assert report.tree() == DRY_PREVIEW_TREE
    assert host.ran_shells == []
    assert host.apt_packages == {}
    assert host.apt_update_stamp is None
    assert f"{C_CLI}/php.ini" not in host.remote_files
    assert text(host, POOL_FILE) == "[www]\nlisten = 127.0.0.1:9000\npm = dynamic\n"
    assert host.services == {}


# --- the outputs channel (design doc sections 9, 10.4, 16.8) ------------------


async def test_a_composing_role_captures_the_relay():
    """``yield from scoped("php", php(v))`` hands the parent the union, by value.

    The set a composing role hands apache's handler is the return value, not a log
    scan (§16.8), and it equals what the three wave reports add up to. The scope
    renames the report's root node and nothing else: same waves, same requests,
    same handler -- §7.2's claim that a scope is a label, not a barrier.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)

    report, captured = await run_and_capture(inventory, hosts)

    assert captured["ubuntu"] == PhpOutputs(
        version=VERSION,
        extension_conf_paths=EXT_PATHS,
        webserver_notified=frozenset({"ubuntu"}),
    )
    # A host outside the family still publishes an output object, with
    # ``version=None``: php-xdebug reads that to return early (§18.7).
    assert captured["web1"] == PhpOutputs(version=None)

    install_rep, cleanup_rep, configure_rep = (
        record.outcome for record in checkpoint_records(report)
    )
    assert captured["ubuntu"].webserver_notified == (
        changed_hosts(install_rep, INSTALL_HANDLER_NAMES)
        | changed_hosts(cleanup_rep, CLEANUP_HANDLER_NAMES)
        | changed_hosts(configure_rep, CONFIGURE_HANDLER_NAMES)
    )
    assert {barrier.scope for barrier in barriers(report)} == {("php",)}
    assert report.tree() == FIRST_PASS_TREE.replace("(root)", "php", 1)


# --- the branches the variables select ---------------------------------------


async def test_the_release_barrier_selects_the_whole_version():
    """One barrier answer moves the packages, every path and the unit name.

    ``vars/Ubuntu-22.yml`` is a table lookup *after* the read, not a variable in
    the inventory: the same role and the same variables install an entirely
    different PHP because the distribution fact said 22.04.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    hosts["ubuntu"].linux_distribution = {
        "name": "Ubuntu",
        "family": "Debian",
        "major": 22,
        "minor": 4,
        "release_meta": {},
    }
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    packages = request_kwargs(report, "Ensure PHP packages are installed")["packages"]
    assert packages[:2] == ("php8.1-common", "php8.1-cli")
    assert "/etc/php/8.1/cli/php.ini" in host.remote_files
    assert "/etc/php/8.1/fpm/pool.d/www.conf" in host.remote_files
    assert host.services["php8.1-fpm.service"] == "active"
    assert "php8.1-fpm.service" in host.enabled_services
    # The 8.3 tree this host was seeded with is not managed on this pass.
    assert f"{C_CLI}/php.ini" not in host.remote_files
    assert "Restart php8.1-fpm (handler)" in handler_names(report)


async def test_an_unknown_release_falls_back_to_the_distribution_default():
    """No ``vars/Ubuntu-26.yml`` means ``include_vars`` lands on Debian's 8.2.

    The barrier answers (a name, an unmapped major); the fallback is a table
    default, not an error, and every downstream path follows it.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    hosts["ubuntu"].linux_distribution = {
        "name": "Ubuntu",
        "family": "Debian",
        "major": 26,
        "minor": 4,
        "release_meta": {},
    }

    report, captured = await run_and_capture(inventory, hosts)

    assert captured["ubuntu"].version == DEBIAN_DEFAULT_PHP_VERSION
    assert captured["ubuntu"].extension_conf_paths == (
        f"/etc/php/{DEBIAN_DEFAULT_PHP_VERSION}/fpm/conf.d",
        f"/etc/php/{DEBIAN_DEFAULT_PHP_VERSION}/apache2/conf.d",
        f"/etc/php/{DEBIAN_DEFAULT_PHP_VERSION}/cli/conf.d",
    )
    assert f"/etc/php/{DEBIAN_DEFAULT_PHP_VERSION}/cli/php.ini" in hosts["ubuntu"].remote_files
    # The seeded grep answers are keyed to 8.3, so the sweep finds nothing here.
    assert entries_named(report, "Remove any non-role-supplied APCu config files") == []


async def test_the_package_var_replaces_and_the_extra_var_appends():
    """``php_packages`` is a replacement, not a merge -- and it drops the json add.

    The one place the role's variable precedence shows: the resolved
    ``__php_packages`` list is discarded wholesale, so the version-specific
    ``php_packages_extra`` list is the only way to keep a package the default list
    had.
    """
    inventory = make_inventory(
        php_packages=["php8.3-cli"],
        php_packages_extra=["php8.3-intl"],
    )
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert request_kwargs(report, "Ensure PHP packages are installed")["packages"] == (
        "php8.3-cli",
        "php8.3-intl",
    )
    assert set(hosts["ubuntu"].apt_packages) == {"php8.3-cli", "php8.3-intl"}
    assert entry_named(report, "Ensure PHP packages are installed").changed


@pytest.mark.parametrize(
    ("state", "present", "latest"),
    [("present", True, False), ("latest", True, True), ("absent", False, False)],
)
async def test_packages_state_maps_onto_the_apt_flags(state, present, latest):
    """Three role states, two apt flags, no branch of its own.

    ``absent`` is faithful: the role never guarded the configuration on the
    package state, so the ini files are still written after the removal wave.
    """
    inventory = make_inventory(php_packages_state=state)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    kwargs = request_kwargs(report, "Ensure PHP packages are installed")
    assert (kwargs["present"], kwargs["latest"]) == (present, latest)
    assert kwargs["no_recommends"] is False
    assert f"{C_CLI}/php.ini" in hosts["ubuntu"].remote_files


async def test_install_recommends_is_the_only_apt_flag_from_a_var():
    inventory = make_inventory(php_install_recommends=False)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    kwargs = request_kwargs(report, "Ensure PHP packages are installed")
    assert kwargs["no_recommends"] is True


async def test_the_path_vars_move_every_managed_file():
    """``php_conf_paths`` and ``php_extension_conf_paths`` are independent lists.

    Overriding the conf paths alone would leave the extension paths at the distro
    default (``vars/Debian.yml`` defines both); here both are given, so every
    write lands in one custom tree -- and that extension list is what
    php-xdebug reads back instead of guessing (§18.7).
    """
    inventory = make_inventory(
        php_conf_paths=["/srv/php/conf"],
        php_extension_conf_paths=["/srv/php/conf.d"],
    )
    hosts = make_hosts(inventory)
    report, captured = await run_and_capture(inventory, hosts)
    host = hosts["ubuntu"]

    assert entries_named(report, "Place PHP configuration file in place")[0].changed
    assert text(host, "/srv/php/conf/php.ini").startswith("[PHP]\n")
    assert len(entries_named(report, "Ensure APCu config file is present")) == 1
    assert text(host, "/srv/php/conf.d/20-apcu.ini").startswith("extension=apcu.so\n")
    assert captured["ubuntu"].extension_conf_paths == ("/srv/php/conf.d",)
    # The seeded 8.3 tree is untouched: no stray sweep hit, no ini written there.
    assert f"{C_CLI}/php.ini" not in host.remote_files
    assert entries_named(report, "Remove any non-role-supplied APCu config files") == []


async def test_use_managed_ini_false_writes_no_php_ini():
    """The variable that deletes a request family rather than a value.

    ``php_use_managed_ini: false`` is how an operator keeps the distro's own
    ``php.ini``; the extension configs the role owns are still written and still
    notify, so the relay is unaffected.
    """
    inventory = make_inventory(php_use_managed_ini=False)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert entries_named(report, "Place PHP configuration file in place") == []
    assert f"{C_CLI}/php.ini" not in hosts["ubuntu"].remote_files
    assert len(entries_named(report, "Ensure OpCache config file is present")) == 3
    assert "configure done → 1 host notified" in report.tree()


async def test_disabling_apc_switches_the_install_to_a_removal():
    """``configure-apcu.yml``'s ``else`` branch, and a name only it uses.

    With ``php_enable_apc: false`` the role writes no APCu ini and instead removes
    the one it would have written, under ``Remove APCu config file if APC is
    disabled`` -- a configure-channel name, distinct from the cleanup wave's
    ``Delete APCu configuration file if this role will provide one``. The packaged
    file is a symlink here, which is what the ``force`` on the removal is for.
    """
    inventory = make_inventory(php_enable_apc=False)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    removals = entries_named(report, "Remove APCu config file if APC is disabled")
    assert len(removals) == 3
    assert all(entry.changed for entry in removals)
    assert entries_named(report, "Ensure APCu config file is present") == []
    assert entries_named(report, CLEANUP_NAMES[0]) == []
    for ext in EXT_PATHS:
        assert f"{ext}/{APC_FILE}" not in host.remote_files
        assert f"{ext}/{APC_FILE}" not in host.links
    assert handler_names(report) == [APACHE_HANDLER, FPM_HANDLER]


async def test_explicit_pools_are_the_template_contexts_themselves():
    """``php_fpm_pools`` is a list of renders, not a tweak of the default pool.

    Two items mean two ``Create fpm pools`` requests in the configure wave, each
    path its own file; the ``{{ php_fpm_* }}`` variables feed only the *default*
    pool, so ``php_fpm_pm_status_path`` does not leak into an item that leaves the
    key out, and an item setting it to ``0`` still gets the template's 500.
    """
    inventory = make_inventory(
        php_fpm_pools=[
            {"pool_name": "app", "pool_listen": "/run/php/app.sock"},
            {
                "pool_name": "cron",
                "pool_listen": "127.0.0.1:9001",
                "pool_pm": "static",
                "pool_pm_max_children": 10,
                "pool_pm_max_requests": 0,
            },
        ]
    )
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    assert len(entries_named(report, "Create fpm pools")) == 2
    app = text(host, f"{POOL_DIR}/app.conf")
    assert "[app]" in app
    assert "listen = /run/php/app.sock" in app
    assert "pm = dynamic" in app
    assert "pm.status_path" not in app
    cron = text(host, f"{POOL_DIR}/cron.conf")
    assert "pm = static" in cron
    assert "pm.max_children = 10" in cron
    assert "pm.max_requests = 500" in cron
    # The default www pool is not managed on this pass: the seeded file survives.
    assert text(host, POOL_FILE) == "[www]\nlisten = 127.0.0.1:9000\npm = dynamic\n"
    assert "configure done → 1 host notified" in report.tree()


# --- the pure helpers (the half of phase 4 the driver never sees) --------------

#: The gathered distribution of the base inventory's Ubuntu host.
DISTRO = {"name": "Ubuntu", "family": "Debian", "major": 24, "minor": 0, "release_meta": {}}


def test_the_release_table_and_its_fallback():
    """``include_vars`` order: the user's var, the release file, Debian's default."""
    for major, expected in UBUNTU_PHP_VERSIONS.items():
        assert php_version(PhpVars(), {"major": major}) == expected
    assert php_version(PhpVars(), {"major": 26}) == DEBIAN_DEFAULT_PHP_VERSION
    assert php_version(PhpVars(), {}) == DEBIAN_DEFAULT_PHP_VERSION
    assert php_version(PhpVars(default_version_debian="8.4"), {"major": 24}) == "8.4"


def test_the_json_package_stops_at_php_8():
    """``main.yml`` appends the version-specific json package below 8.0 only."""
    old = resolve_php(PhpVars(), {"name": "Ubuntu", "major": 20})
    assert old.version == "7.4"
    assert old.packages[-1] == "php7.4-json"
    assert old.fpm_daemon == "php7.4-fpm"
    new = resolve_php(PhpVars(), DISTRO)
    assert [package for package in new.packages if package.endswith("-json")] == []


def test_the_unspecified_paths_come_from_the_version():
    """Every ``__php_*`` fallback the ``set_fact`` tasks applied, in one object."""
    rv = resolve_php(PhpVars(), DISTRO)
    assert rv.conf_paths == CONF_PATHS
    assert rv.ext_paths == EXT_PATHS
    assert rv.pool_conf_path == POOL_FILE
    assert rv.apc_filename == APC_FILE
    assert rv.opcache_filename == OPCACHE_FILE
    assert (rv.webserver_daemon, rv.fpm_daemon) == ("apache2", f"php{VERSION}-fpm")
    assert (rv.pool_user, rv.pool_group) == ("www-data", "www-data")
    assert rv.distribution_name == "Ubuntu"


def test_install_plan_carries_only_the_apt_flags():
    """The install stage's whole input: a list and three booleans."""
    rv = resolve_php(PhpVars(), DISTRO)
    assert install_plan(PhpVars(), rv).packages == rv.packages
    for state, present, latest in (
        ("present", True, False),
        ("latest", True, True),
        ("absent", False, False),
    ):
        plan = install_plan(PhpVars(packages_state=state), rv)
        assert (plan.present, plan.latest) == (present, latest)
    assert install_plan(PhpVars(install_recommends=False), rv).no_recommends is True


def test_the_cleanup_plan_follows_the_extension_switches():
    """``setup-Debian.yml`` deletes a packaged ini only if it will replace it."""
    rv = resolve_php(PhpVars(), DISTRO)
    plan = packaged_cleanup_plan(PhpVars(enable_php_fpm=True), rv)
    assert plan.apc_removals == tuple(f"{ext}/{APC_FILE}" for ext in EXT_PATHS)
    assert plan.opcache_removals == tuple(f"{ext}/{OPCACHE_FILE}" for ext in EXT_PATHS)
    off = packaged_cleanup_plan(PhpVars(enable_apc=False, opcache_enable="0"), rv)
    assert off.apc_removals == ()
    assert off.opcache_removals == ()


def test_configure_plan_freezes_every_read():
    """The plan is a pure function of the barrier answers, with nothing left to ask.

    Empty sweeps and a present pool directory -- the converged host's answers --
    mean no stray removals and no directory target: exactly why the second pass
    can plan the same sequence it planned first and change nothing.
    """
    v = PhpVars(enable_php_fpm=True)
    rv = resolve_php(v, DISTRO)
    plan = configure_plan(v, rv, (), (), pool_dir_exists=True)

    assert plan.stray_apcu == ()
    assert plan.stray_opcache == ()
    assert plan.pool_dir is None
    assert plan.directories == rv.conf_paths + rv.ext_paths
    assert plan.php_ini == tuple(
        (f"{path}/php.ini", render_php_ini(v)) for path in rv.conf_paths
    )
    pool = default_pools(v)[0]
    assert plan.pools == ((POOL_FILE, render_www_conf(pool, rv)),)
    service = plan.fpm_service
    assert (service.service, service.running, service.enabled) == (
        f"php{VERSION}-fpm",
        True,
        True,
    )
    assert (service.restarted, service.reloaded) == (False, False)

    absent = configure_plan(v, rv, (STRAY_APCU,), (STRAY_OPCACHE,), pool_dir_exists=False)
    assert absent.pool_dir == POOL_DIR
    assert absent.stray_apcu == (STRAY_APCU,)
    assert absent.stray_opcache == (STRAY_OPCACHE,)


def test_a_debian_name_suppresses_the_service_task():
    """The role guards the FPM service task on ``distribution != "Debian"``.

    The body's family filter normally excludes such a host; the guard is kept
    because an inventory entry may say ``Ubuntu`` while the gathered fact says
    otherwise. The pools themselves are still written.
    """
    v = PhpVars(enable_php_fpm=True)
    rv = resolve_php(v, {"name": "Debian", "family": "Debian", "major": 12, "minor": 0})
    plan = configure_plan(v, rv, (), (), pool_dir_exists=True)

    assert rv.distribution_name == "Debian"
    assert plan.fpm_service is None
    assert plan.pools


def test_stray_configs_recovers_find_semantics():
    """Shallow, non-hidden, never the managed filename -- and sorted.

    ``grep -r`` recurses where ``find`` is shallow, matches dotfiles where
    ``find`` skips hidden, and hits the role's own file too; the plan filters all
    three back out, then sorts so the request order is not the filesystem's.
    """
    hits = (
        STRAY_APCU,
        HIDDEN_APCU,
        NESTED_APCU,
        f"{E_CLI}/{APC_FILE}",
        f"{C_FPM}/top.ini",
        f"{E_APACHE}/aaa-apcu.ini",
    )
    assert stray_configs(hits, EXT_PATHS, APC_FILE) == (
        f"{E_APACHE}/aaa-apcu.ini",
        STRAY_APCU,
    )
    # A hit outside every searched directory is not this role's business.
    assert stray_configs((f"/etc/php/{VERSION}/conf.d/x-apcu.ini",), EXT_PATHS, APC_FILE) == ()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", True),
        ("0", False),
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("yes", True),
        ("no", False),
        ("On", True),
        ("OFF", False),
    ],
)
def test_opcache_enable_is_read_the_ansible_way(value, expected):
    """``| bool`` on a value that also renders verbatim: YAML 1.1, case-insensitive."""
    assert opcache_enabled(PhpVars(opcache_enable=value)) is expected


def test_a_non_boolean_opcache_switch_is_an_error():
    """``| bool`` is not ``| ternary``: Ansible would warn, this raises early."""
    for value, message in (
        ("", "php_opcache_enable must be a boolean"),
        ("maybe", "must be a boolean"),
    ):
        with pytest.raises(ValueError, match=message):
            opcache_enabled(PhpVars(opcache_enable=value))
    with pytest.raises(ValueError, match="php_x must be a boolean, got 2"):
        _ansible_bool(2, "php_x")


def test_render_php_ini_is_substitution_plus_join():
    v = PhpVars(disable_functions=("passthru", "exec"), memory_limit="1G", date_timezone="UTC")
    ini = render_php_ini(v)

    assert ini.startswith("[PHP]\n")
    assert "disable_functions = passthru,exec\n" in ini
    assert "memory_limit = 1G\n" in ini
    assert "date.timezone = UTC\n" in ini
    assert ini.endswith("ldap.max_links = -1\n")


def test_the_extension_ini_renders_carry_their_conditionals():
    """``apc.ini.j2`` is pure substitution; ``opcache.ini.j2`` has one ``{% if %}``."""
    assert render_apc_ini(PhpVars(apc_shm_size="128M", apc_enable_cli="1")) == (
        "extension=apcu.so\napc.shm_size=128M\napc.enable_cli=1\napc.rfc1867=1\n"
    )
    plain = render_opcache_ini(PhpVars())
    assert plain.startswith("zend_extension=opcache.so\nopcache.enable=1\n")
    assert plain.endswith("opcache.max_file_size=0\n")
    assert "blacklist" not in plain
    listed = render_opcache_ini(PhpVars(opcache_blacklist_filename="/etc/php/bl"))
    assert listed.endswith("opcache.blacklist_filename=/etc/php/bl\n")


def test_render_www_conf_uses_the_falsy_defaults():
    """``default(x, true)`` everywhere, plus the one conditional line."""
    rv = resolve_php(PhpVars(), DISTRO)
    bare = render_www_conf(PhpFpmPool(pool_name="www", pool_listen="x"), rv)

    assert bare.startswith(f"; {ANSIBLE_MANAGED}\n\n[www]\nlisten = x\n")
    assert "pm.max_requests = 500\n" in bare  # the template default, not ``0``
    assert "pm.status_path" not in bare
    assert "listen.allowed_clients = 127.0.0.1\n" in bare

    tuned = render_www_conf(
        PhpFpmPool(
            pool_name="app",
            pool_listen="/run/php/app.sock",
            pool_listen_allowed_clients="10.0.0.1",
            pool_pm="static",
            pool_pm_max_children=10,
            pool_pm_max_requests=200,
            pool_pm_status_path="/status",
        ),
        rv,
    )
    assert "[app]\n" in tuned
    assert "pm = static\n" in tuned
    assert "pm.max_children = 10\n" in tuned
    assert "pm.max_requests = 200\n" in tuned
    assert "pm.status_path = /status\n" in tuned
    assert "listen.allowed_clients = 10.0.0.1\n" in tuned
    assert "user = www-data\ngroup = www-data\n" in tuned


def test_the_three_channels_have_no_name_in_common():
    """The reports are selected by request name, so the names must not overlap.

    If one name sat in two waves, ``changed_hosts`` would answer for a stage the
    checkpoint never closed and the union would be a lie. The pool task is the
    deliberate exception in the other direction: it notifies php-fpm directly and
    is in no webserver channel at all.
    """
    assert INSTALL_HANDLER_NAMES == PACKAGE_GATE_NAMES
    assert not PACKAGE_GATE_NAMES & CLEANUP_HANDLER_NAMES
    assert not PACKAGE_GATE_NAMES & CONFIGURE_HANDLER_NAMES
    assert not CLEANUP_HANDLER_NAMES & CONFIGURE_HANDLER_NAMES
    assert not FPM_HANDLER_NAMES & WEBSERVER_HANDLER_NAMES
    assert WEBSERVER_HANDLER_NAMES == (
        INSTALL_HANDLER_NAMES | CLEANUP_HANDLER_NAMES | CONFIGURE_HANDLER_NAMES
    )


# --- the variable model -------------------------------------------------------


def test_php_vars_is_frozen_and_closed():
    """``frozen=True`` plus ``extra="forbid"``: the resolution cannot drift.

    A body that could write back to its own vars could disagree with the
    determinism pass that replays it, and an Ansible name this role never
    implemented (``php_enable_apcu``) must fail rather than silently noop.
    """
    v = PhpVars()
    with pytest.raises(ValidationError, match="frozen"):
        v.precision = "1"
    with pytest.raises(ValidationError):
        PhpVars(enable_apcu=True)


def test_a_typoed_role_variable_is_named():
    """A prefixed key that names no field is a spelling mistake, not an ignore."""
    inventory = [
        {
            "host": "ubuntu",
            "family": "Ubuntu",
            "connection": {"address": "192.0.2.10"},
            "php_memory_limi": "512M",
        }
    ]

    with pytest.raises(RoleVarsError, match="php_memory_limi"):
        resolve_vars(PhpVars, "php_", inventory)


def test_the_choice_variables_are_closed():
    """Every Literal here is a value the task list actually branches on."""
    for kwargs in (
        {"packages_state": "installed"},
        {"fpm_state": "enabled"},
        {"fpm_handler_state": "stopped"},
        {"default_version_debian": "8.x"},
    ):
        with pytest.raises(ValidationError):
            PhpVars(**kwargs)


def test_pool_items_follow_the_template_filters():
    """``| mandatory`` becomes a non-empty required field; the template is a gate.

    ``extra="allow"`` is the deliberate half: a key ``www.conf.j2`` has not
    learned yet survives the model instead of being rejected, so the render, not
    the schema, decides what a pool item means.
    """
    with pytest.raises(ValidationError):
        PhpFpmPool(pool_listen="/run/php/x.sock")
    with pytest.raises(ValidationError, match="non-empty pool_name"):
        PhpFpmPool(pool_name="", pool_listen="/run/php/x.sock")
    with pytest.raises(ValidationError, match="non-empty pool_listen"):
        PhpFpmPool(pool_name="www", pool_listen="")
    with pytest.raises(ValidationError, match="www.conf.j2"):
        PhpFpmPool(pool_name="www", pool_listen="x", pool_template="other.j2")
    pool = PhpFpmPool(pool_name="www", pool_listen="x", pool_idle_timeout="60")
    assert pool.model_extra == {"pool_idle_timeout": "60"}


def test_a_bad_pool_item_in_the_inventory_is_refused_before_the_run():
    """The pool list is validated with the rest of the vars, not at the render."""
    inventory = [
        {
            "host": "ubuntu",
            "family": "Ubuntu",
            "connection": {"address": "192.0.2.10"},
            "php_enable_php_fpm": True,
            "php_fpm_pools": [{"pool_listen": "/run/php/x.sock"}],
        }
    ]

    with pytest.raises(ValidationError):
        resolve_vars(PhpVars, "php_", inventory)


def test_numbers_arrive_as_jinja_would_render_them():
    """``coerce_numbers_to_str`` everywhere except the one verbatim gate.

    ``php_memory_limit: 512`` and ``php_opcache_enable: 1`` are both legal YAML.
    Jinja renders the first as a plain string, so the field is ``str``; the second
    renders *and* gates through ``| bool``, so :attr:`PhpVars.opcache_enable`
    keeps the raw value -- a declared ``bool`` field would render ``True`` where
    the template writes ``1``.
    """
    v = PhpVars(memory_limit=512, max_execution_time=0, precision=14)

    assert (v.memory_limit, v.max_execution_time, v.precision) == ("512", "0", "14")
    assert "memory_limit = 512\n" in render_php_ini(v)
    assert PhpVars(opcache_enable=0).opcache_enable == 0
    assert render_opcache_ini(PhpVars(opcache_enable=0)).startswith(
        "zend_extension=opcache.so\nopcache.enable=0\n"
    )


# --- the wave tree (design doc section 7.2) -----------------------------------

# Pinned from a real simulated run (``pytest tests/roles/test_php.py -k snapshot``
# prints the diff against them). The install spool directories are
# content-addressed, so the hashes are stable; the barrier lines carry the raw
# fact answer, which is why the distribution and the two sweep results are long
# single lines. Twelve waves on the first pass, eleven on the converged one: the
# handler wave is the only thing that disappears.
FIRST_PASS_TREE = """(root)
  wave 0
    barrier: LinuxDistribution → {'name': 'Ubuntu', 'family': 'Debian', 'major': 24, 'minor': 0, 'release_meta': {}}
  wave 1
    ubuntu: Update apt cache         [changed: apt update]
        $ sudo apt-get update
    ubuntu: Ensure PHP packages are installed [changed: 13 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install php8.3-common php8.3-cli php8.3-dev php8.3-fpm libpcre3-dev php8.3-gd php8.3-curl php8.3-imap php8.3-opcache php8.3-xml php8.3-mbstring php8.3-apcu php8.3-sqlite3
    barrier: install done → 1 host notified
  wave 2
    ubuntu: Delete APCu configuration file if this role will provide one [changed: /etc/php/8.3/fpm/conf.d/20-apcu.ini present, will remove]
        $ sudo rm -f /etc/php/8.3/fpm/conf.d/20-apcu.ini
    ubuntu: Delete APCu configuration file if this role will provide one [changed: /etc/php/8.3/apache2/conf.d/20-apcu.ini is not a file, will remove first; file /etc/php/8.3/apache2/conf.d/20-apcu.ini does not exist]
        $ sudo rm -rf /etc/php/8.3/apache2/conf.d/20-apcu.ini
    ubuntu: Delete APCu configuration file if this role will provide one [changed: /etc/php/8.3/cli/conf.d/20-apcu.ini is not a file, will remove first; file /etc/php/8.3/cli/conf.d/20-apcu.ini does not exist]
        $ sudo rm -rf /etc/php/8.3/cli/conf.d/20-apcu.ini
    ubuntu: Delete OpCache configuration file if this role will provide one [changed: /etc/php/8.3/fpm/conf.d/10-opcache.ini is not a file, will remove first; file /etc/php/8.3/fpm/conf.d/10-opcache.ini does not exist]
        $ sudo rm -rf /etc/php/8.3/fpm/conf.d/10-opcache.ini
    ubuntu: Delete OpCache configuration file if this role will provide one [changed: /etc/php/8.3/apache2/conf.d/10-opcache.ini present, will remove]
        $ sudo rm -f /etc/php/8.3/apache2/conf.d/10-opcache.ini
    ubuntu: Delete OpCache configuration file if this role will provide one [changed: /etc/php/8.3/cli/conf.d/10-opcache.ini is not a file, will remove first; file /etc/php/8.3/cli/conf.d/10-opcache.ini does not exist]
        $ sudo rm -rf /etc/php/8.3/cli/conf.d/10-opcache.ini
    barrier: cleanup done → 1 host notified
  wave 3
    barrier: FindInFiles → ['/etc/php/8.3/fpm/conf.d/20-apcu.ini']
  wave 4
    barrier: FindInFiles → []
  wave 5
    barrier: FindInFiles → []
  wave 6
    barrier: FindInFiles → ['/etc/php/8.3/apache2/conf.d/10-opcache.ini', '/etc/php/8.3/apache2/conf.d/stray-opcache.ini']
  wave 7
    barrier: FindInFiles → ['/etc/php/8.3/cli/conf.d/.hidden-apcu.ini', '/etc/php/8.3/cli/conf.d/nested/old-apcu.ini', '/etc/php/8.3/cli/conf.d/stray-apcu.ini', '/etc/php/8.3/cli/conf.d/zz-commented.ini']
  wave 8
    barrier: FindInFiles → []
  wave 9
    barrier: FileExists → True
  wave 10
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/fpm already exists]
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/apache2 already exists]
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/cli already exists]
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/fpm/conf.d already exists]
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/apache2/conf.d already exists]
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/cli/conf.d already exists]
    ubuntu: Place PHP configuration file in place [changed: content /etc/php/8.3/fpm/php.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-8d471761f3da1a8b/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/fpm && cp /tmp/reemote-install-8d471761f3da1a8b/content /etc/php/8.3/fpm/php.ini && chown root:root /etc/php/8.3/fpm/php.ini && chmod 644 /etc/php/8.3/fpm/php.ini && rm -rf /tmp/reemote-install-8d471761f3da1a8b'
    ubuntu: Place PHP configuration file in place [changed: content /etc/php/8.3/apache2/php.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-f4df07cbd5c858a9/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/apache2 && cp /tmp/reemote-install-f4df07cbd5c858a9/content /etc/php/8.3/apache2/php.ini && chown root:root /etc/php/8.3/apache2/php.ini && chmod 644 /etc/php/8.3/apache2/php.ini && rm -rf /tmp/reemote-install-f4df07cbd5c858a9'
    ubuntu: Place PHP configuration file in place [changed: content /etc/php/8.3/cli/php.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-7fcbaa98f7a007e5/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/cli && cp /tmp/reemote-install-7fcbaa98f7a007e5/content /etc/php/8.3/cli/php.ini && chown root:root /etc/php/8.3/cli/php.ini && chmod 644 /etc/php/8.3/cli/php.ini && rm -rf /tmp/reemote-install-7fcbaa98f7a007e5'
    ubuntu: Remove any non-role-supplied APCu config files [changed: /etc/php/8.3/cli/conf.d/stray-apcu.ini present, will remove]
        $ sudo rm -f /etc/php/8.3/cli/conf.d/stray-apcu.ini
    ubuntu: Remove any non-role-supplied APCu config files [changed: /etc/php/8.3/cli/conf.d/zz-commented.ini present, will remove]
        $ sudo rm -f /etc/php/8.3/cli/conf.d/zz-commented.ini
    ubuntu: Remove any non-role-supplied OpCache config files [changed: /etc/php/8.3/apache2/conf.d/stray-opcache.ini present, will remove]
        $ sudo rm -f /etc/php/8.3/apache2/conf.d/stray-opcache.ini
    ubuntu: Ensure APCu config file is present [changed: content /etc/php/8.3/fpm/conf.d/20-apcu.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-3b5aab3fe5a37fb1/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/fpm/conf.d && cp /tmp/reemote-install-3b5aab3fe5a37fb1/content /etc/php/8.3/fpm/conf.d/20-apcu.ini && chown root:root /etc/php/8.3/fpm/conf.d/20-apcu.ini && chmod 644 /etc/php/8.3/fpm/conf.d/20-apcu.ini && rm -rf /tmp/reemote-install-3b5aab3fe5a37fb1'
    ubuntu: Ensure APCu config file is present [changed: content /etc/php/8.3/apache2/conf.d/20-apcu.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-f8189c511495c305/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/apache2/conf.d && cp /tmp/reemote-install-f8189c511495c305/content /etc/php/8.3/apache2/conf.d/20-apcu.ini && chown root:root /etc/php/8.3/apache2/conf.d/20-apcu.ini && chmod 644 /etc/php/8.3/apache2/conf.d/20-apcu.ini && rm -rf /tmp/reemote-install-f8189c511495c305'
    ubuntu: Ensure APCu config file is present [changed: content /etc/php/8.3/cli/conf.d/20-apcu.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-58cf0d063c112510/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/cli/conf.d && cp /tmp/reemote-install-58cf0d063c112510/content /etc/php/8.3/cli/conf.d/20-apcu.ini && chown root:root /etc/php/8.3/cli/conf.d/20-apcu.ini && chmod 644 /etc/php/8.3/cli/conf.d/20-apcu.ini && rm -rf /tmp/reemote-install-58cf0d063c112510'
    ubuntu: Ensure OpCache config file is present [changed: content /etc/php/8.3/fpm/conf.d/10-opcache.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-b785ad1a5215c134/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/fpm/conf.d && cp /tmp/reemote-install-b785ad1a5215c134/content /etc/php/8.3/fpm/conf.d/10-opcache.ini && chown root:root /etc/php/8.3/fpm/conf.d/10-opcache.ini && chmod 644 /etc/php/8.3/fpm/conf.d/10-opcache.ini && rm -rf /tmp/reemote-install-b785ad1a5215c134'
    ubuntu: Ensure OpCache config file is present [changed: content /etc/php/8.3/apache2/conf.d/10-opcache.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-33945299ae856c39/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/apache2/conf.d && cp /tmp/reemote-install-33945299ae856c39/content /etc/php/8.3/apache2/conf.d/10-opcache.ini && chown root:root /etc/php/8.3/apache2/conf.d/10-opcache.ini && chmod 644 /etc/php/8.3/apache2/conf.d/10-opcache.ini && rm -rf /tmp/reemote-install-33945299ae856c39'
    ubuntu: Ensure OpCache config file is present [changed: content /etc/php/8.3/cli/conf.d/10-opcache.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-1e2b5a2439f9a37f/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/cli/conf.d && cp /tmp/reemote-install-1e2b5a2439f9a37f/content /etc/php/8.3/cli/conf.d/10-opcache.ini && chown root:root /etc/php/8.3/cli/conf.d/10-opcache.ini && chmod 644 /etc/php/8.3/cli/conf.d/10-opcache.ini && rm -rf /tmp/reemote-install-1e2b5a2439f9a37f'
    ubuntu: Create fpm pools         [changed: content /etc/php/8.3/fpm/pool.d/www.conf differs, will install]
        $ scp <files install> -> /tmp/reemote-install-996094595c368e8a/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/fpm/pool.d && cp /tmp/reemote-install-996094595c368e8a/content /etc/php/8.3/fpm/pool.d/www.conf && chown root:root /etc/php/8.3/fpm/pool.d/www.conf && chmod 644 /etc/php/8.3/fpm/pool.d/www.conf && rm -rf /tmp/reemote-install-996094595c368e8a'
    ubuntu: Ensure php-fpm is started and enabled at boot (if configured) [changed: php8.3-fpm.service not running (None), will start; php8.3-fpm.service not enabled, will enable]
        $ sudo systemctl start php8.3-fpm.service
        $ sudo systemctl enable php8.3-fpm.service
    barrier: configure done → 1 host notified
  wave 11
    ubuntu: Restart apache2 (handler) [changed: apache2.service not running (None), will start]
        $ sudo systemctl start apache2.service
    ubuntu: Restart php8.3-fpm (handler) [changed: php8.3-fpm.service already running; php8.3-fpm.service will restart]
        $ sudo systemctl restart php8.3-fpm.service"""

CONVERGED_TREE = """(root)
  wave 0
    barrier: LinuxDistribution → {'name': 'Ubuntu', 'family': 'Debian', 'major': 24, 'minor': 0, 'release_meta': {}}
  wave 1
    ubuntu: Update apt cache         [no change: apt update skipped, cache is fresh]
    ubuntu: Ensure PHP packages are installed [no change: all packages present]
    barrier: install done → 0 hosts notified
  wave 2
    barrier: cleanup done → 0 hosts notified
  wave 3
    barrier: FindInFiles → ['/etc/php/8.3/fpm/conf.d/20-apcu.ini']
  wave 4
    barrier: FindInFiles → []
  wave 5
    barrier: FindInFiles → []
  wave 6
    barrier: FindInFiles → ['/etc/php/8.3/apache2/conf.d/10-opcache.ini', '/etc/php/8.3/apache2/conf.d/stray-opcache.ini']
  wave 7
    barrier: FindInFiles → ['/etc/php/8.3/cli/conf.d/.hidden-apcu.ini', '/etc/php/8.3/cli/conf.d/nested/old-apcu.ini', '/etc/php/8.3/cli/conf.d/stray-apcu.ini', '/etc/php/8.3/cli/conf.d/zz-commented.ini']
  wave 8
    barrier: FindInFiles → []
  wave 9
    barrier: FileExists → True
  wave 10
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/fpm already exists]
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/apache2 already exists]
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/cli already exists]
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/fpm/conf.d already exists]
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/apache2/conf.d already exists]
    ubuntu: Ensure configuration directories exist [no change: directory /etc/php/8.3/cli/conf.d already exists]
    ubuntu: Place PHP configuration file in place [no change: content already up to date (/etc/php/8.3/fpm/php.ini)]
    ubuntu: Place PHP configuration file in place [no change: content already up to date (/etc/php/8.3/apache2/php.ini)]
    ubuntu: Place PHP configuration file in place [no change: content already up to date (/etc/php/8.3/cli/php.ini)]
    ubuntu: Remove any non-role-supplied APCu config files [no change: file /etc/php/8.3/cli/conf.d/stray-apcu.ini does not exist]
    ubuntu: Remove any non-role-supplied APCu config files [no change: file /etc/php/8.3/cli/conf.d/zz-commented.ini does not exist]
    ubuntu: Remove any non-role-supplied OpCache config files [no change: file /etc/php/8.3/apache2/conf.d/stray-opcache.ini does not exist]
    ubuntu: Ensure APCu config file is present [no change: content already up to date (/etc/php/8.3/fpm/conf.d/20-apcu.ini)]
    ubuntu: Ensure APCu config file is present [no change: content already up to date (/etc/php/8.3/apache2/conf.d/20-apcu.ini)]
    ubuntu: Ensure APCu config file is present [no change: content already up to date (/etc/php/8.3/cli/conf.d/20-apcu.ini)]
    ubuntu: Ensure OpCache config file is present [no change: content already up to date (/etc/php/8.3/fpm/conf.d/10-opcache.ini)]
    ubuntu: Ensure OpCache config file is present [no change: content already up to date (/etc/php/8.3/apache2/conf.d/10-opcache.ini)]
    ubuntu: Ensure OpCache config file is present [no change: content already up to date (/etc/php/8.3/cli/conf.d/10-opcache.ini)]
    ubuntu: Create fpm pools         [no change: content already up to date (/etc/php/8.3/fpm/pool.d/www.conf)]
    ubuntu: Ensure php-fpm is started and enabled at boot (if configured) [no change: php8.3-fpm.service already running; php8.3-fpm.service already enabled]
    barrier: configure done → 0 hosts notified"""

DRY_PREVIEW_TREE = """(root)
  wave 0
    barrier: LinuxDistribution → unknown until run"""


async def test_tree_snapshot():
    """The whole role as one tree: two gated waves, six sweeps, one handler wave.

    This is the shape §18.6 had to prove and the reason the migration is visible
    at all: three checkpoints sit inside the waves they report on, so the tree
    shows eleven barrier lines instead of the old example's four ``run()`` calls.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)

    assert first.tree() == FIRST_PASS_TREE

    second = await run_role(inventory, hosts)
    assert second.tree() == CONVERGED_TREE
