# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end test for the PHP Ansible-role migration example.

``examples/roles/ansible-role-php/ansible-role-php.py`` connects for real when
run as a script, so the suite never executes its ``main()``. What it *can* do
offline is replay the same orchestration against ``SimulatedHost``: the example
is imported as a module, its factories are fed snapshots gathered from
simulated state, and every deploy goes through the real driver (prepare +
apply, plus a ``dry=True`` preview).

The load-bearing assertion is the migration's idempotence claim: run the whole
install -> packaged-cleanup -> sweep -> configure -> handler sequence, then run
it *again* against the same persistent hosts, and the second pass must report
``changed=False`` everywhere and queue no handler.

Two between-pass bridges are needed, both *simulation* limits rather than
library bugs:

* ``SimulatedHost.make_directory`` records a ``directory_stats`` row but never
  adds the path to ``existing_files`` (the ``FileExists`` seam). The pool
  directory is created by deploy 3, so ``settle()`` syncs the two stores before
  pass 2 -- otherwise the pool-dir existence read would still answer False and
  the create request would re-fire.
* The ``FindInFiles`` seam (``grep_files``) is keyed by the question and is not
  maintained by the file mutators, so ``settle()`` re-derives the hits from the
  simulated filesystem after every pass. The role's two regexes are plain
  enough (``\\s``, ``\\.``, one optional class) that Python ``re.search``
  reproduces ``grep -E`` line-for-line; ``refresh_grep`` scans only
  ``remote_files`` (regular files), which naturally skips the packaged-ini
  symlinks exactly as ``grep -r`` does.

Seeding choices, not bridges: the simulated apt install records package names
only, so the packaged APCu/OpCache ini files (a mix of symlinks and regular
files, to exercise both ``files.file`` removal branches) and the conf directory
tree are seeded as the packages' post-install state -- the cleanup and
configure deploys run after the install apply, when a real machine has them.
The ``file_stats`` row for every symlink is ``False`` so the stat-shaped ``File``
fact reads it as a non-file and ``force=True`` removes the link itself.
"""

import importlib.util
import posixpath
import re
from pathlib import Path
from typing import Any

import pytest

from reemote import (
    FileExists,
    FindInFiles,
    LinuxDistribution,
    changed_hosts,
    gather_fact,
    run,
)
from tests.simulated_host import SimulatedHost

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = (
    REPO_ROOT / "examples" / "roles" / "ansible-role-php" / "ansible-role-php.py"
)


def load_example() -> Any:
    """Import the migration example by path (its name is not a valid identifier)."""
    spec = importlib.util.spec_from_file_location("ansible_role_php_example", EXAMPLE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


php = load_example()

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
MODS = f"/etc/php/{VERSION}/mods-available"
APC_FILE = "20-apcu.ini"
OPCACHE_FILE = "10-opcache.ini"

# The pre-state stray configs the FindInFiles sweep must classify.
STRAY_APCU = f"{E_CLI}/stray-apcu.ini"
HIDDEN_APCU = f"{E_CLI}/.hidden-apcu.ini"
NESTED_APCU = f"{E_CLI}/nested/old-apcu.ini"
COMMENTED_APCU = f"{E_CLI}/zz-commented.ini"
STRAY_OPCACHE = f"{E_APACHE}/stray-opcache.ini"


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride as extra keys -- reemote's ``host_vars`` -- so
    ``role_settings`` reads them straight off ``host.extra``. The overrides
    exercise the non-default branches: PHP-FPM enabled (pools + service + the
    chained handler), a tuned memory limit, and the two conditional template
    lines (the FPM status path and the OpCache blacklist).
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
    ``files.file(present=False, force=True)`` take the remove-the-link branch.
    """
    host.links[path] = target
    host.link_stats[path] = {
        "mode": 777,
        "user": "root",
        "group": "root",
        "link_target": target,
    }
    host.file_stats[path] = False


def refresh_grep(host: SimulatedHost) -> None:
    """Re-derive the ``FindInFiles`` answers from the simulated filesystem.

    ``grep -rlE`` recurses, includes dotfiles and matches anywhere in a line;
    it skips symlinks during recursion. Scanning ``remote_files`` (regular
    files only) under each ext path reproduces all of that for the role's two
    plain regexes. The control-side ``stray_configs`` filter then recovers
    ``find``'s shallow, non-hidden semantics.
    """
    host.grep_files = {}
    for ext in EXT_PATHS:
        for pattern in (php.APCU_PATTERN, php.OPCACHE_PATTERN):
            hits = [
                path
                for path, data in host.remote_files.items()
                if path.startswith(ext + "/")
                and any(
                    re.search(pattern, line) for line in data.decode().splitlines()
                )
            ]
            host.grep_files[(ext, pattern)] = sorted(hits)


def seed(host: SimulatedHost) -> None:
    """Pre-state: a stock Ubuntu 24.04 machine with PHP not yet installed.

    The packages' own files (the conf directory tree and the packaged
    APCu/OpCache ini files) are seeded as their post-install state: the
    simulation's apt install records package names only, and the cleanup and
    configure deploys run after the install apply, when a real machine has them.
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
        for path in (*CONF_PATHS, *EXT_PATHS, MODS, f"{E_CLI}/nested")
    }
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
    _regular_file(host, f"{E_FPM}/{APC_FILE}", b"extension=apcu.so\n; packaged\n")
    _symlink(host, f"{E_FPM}/{OPCACHE_FILE}", f"{MODS}/opcache.ini")
    _symlink(host, f"{E_CLI}/{OPCACHE_FILE}", f"{MODS}/opcache.ini")
    _regular_file(
        host, f"{E_APACHE}/{OPCACHE_FILE}", b"zend_extension=opcache.so\n; packaged\n"
    )

    # Stray configs the sweep must remove (direct, non-hidden, unmanaged) and
    # the ones it must keep (a dotfile, a nested file). The commented line
    # matches only because grep matches anywhere in a line -- the documented
    # deviation from find's line-start-anchored contains.
    _regular_file(host, STRAY_APCU, b"extension=apcu.so\n")
    _regular_file(host, HIDDEN_APCU, b"extension=apcu.so\n")
    _regular_file(host, NESTED_APCU, b"extension=apcu.so\n")
    _regular_file(host, COMMENTED_APCU, b"; extension=apcu.so (disabled)\n")
    _regular_file(host, STRAY_OPCACHE, b"zend_extension=opcache.so\n")

    refresh_grep(host)


def settle(host: SimulatedHost) -> None:
    """Bridge the subsystems the simulation does not maintain, between passes."""
    # make_directory writes directory_stats but not existing_files; FileExists
    # (the pool-dir gate) reads the latter.
    host.existing_files |= set(host.directory_stats)
    # The grep seam is question-keyed and no mutator refreshes it.
    refresh_grep(host)


def make_hosts(inventory: list[dict[str, Any]]) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if php.is_ubuntu(host):
            seed(host)
        hosts[host.name] = host
    return hosts


async def migrate(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SimulatedHost],
    *,
    dry: bool = True,
) -> dict[str, Any]:
    """Replay the example's whole orchestration once, against persistent hosts.

    The sequence mirrors ``main()`` barrier for barrier -- the distribution
    read sits before deploy 1, the stray sweeps and the pool-dir stat sit
    between the cleanup and configure runs -- but reads the simulated hosts
    instead of opening SSH connections.
    """
    targets = [host for host in hosts.values() if php.is_ubuntu(host)]
    settings = {host.name: php.role_settings(host) for host in targets}
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

    # The distribution major selects the PHP version table, before deploy 1.
    try:
        distributions = await gather_fact(targets, LinuxDistribution)
    finally:
        for host in targets:
            await host.aclose()

    # Deploy 1: the apt cache refresh and the packages.
    install_plans = {
        host.name: php.install_plan(settings[host.name], distributions[host.name])
        for host in targets
    }
    install_report = await execute(php.build_install(install_plans))

    # register: php_package_install -> when: ...changed gates deploy 2.
    package_changed = changed_hosts(install_report, ("Ensure PHP packages are installed",))
    cleanup_plans = {
        host.name: php.packaged_cleanup_plan(settings[host.name], distributions[host.name])
        for host in targets
    }
    cleanup_report = await execute(php.build_packaged_cleanup(cleanup_plans, package_changed))

    # The stray sweeps and the pool-dir stat, after the cleanup apply.
    apcu_hits: dict[str, tuple[str, ...]] = {}
    opcache_hits: dict[str, tuple[str, ...]] = {}
    pool_exists: dict[str, bool] = {}
    try:
        for host in targets:
            rv = php.resolved_vars(settings[host.name], distributions[host.name])
            apcu: list[str] = []
            opcache: list[str] = []
            for ext in rv["ext_paths"]:
                apcu += (await gather_fact([host], FindInFiles, ext, php.APCU_PATTERN))[host.name]
                opcache += (await gather_fact([host], FindInFiles, ext, php.OPCACHE_PATTERN))[
                    host.name
                ]
            apcu_hits[host.name] = tuple(apcu)
            opcache_hits[host.name] = tuple(opcache)
            pool_dir = posixpath.dirname(rv["pool_conf_path"])
            pool_exists[host.name] = bool(
                (await gather_fact([host], FileExists, pool_dir))[host.name]
            )
    finally:
        for host in targets:
            await host.aclose()

    # Freeze the configure plan and run deploy 3.
    configure_plans = {
        host.name: php.configure_plan(
            settings[host.name],
            distributions[host.name],
            apcu_hits[host.name],
            opcache_hits[host.name],
            pool_exists[host.name],
        )
        for host in targets
    }
    configure_report = await execute(php.build_configure(configure_plans))

    # The handlers: the union of notifying names across the three reports, then
    # the role's two when guards, then the chained FPM set.
    webserver_notified = (
        changed_hosts(install_report, php.WEBSERVER_NOTIFY_NAMES)
        | changed_hosts(cleanup_report, php.WEBSERVER_NOTIFY_NAMES)
        | changed_hosts(configure_report, php.WEBSERVER_NOTIFY_NAMES)
    )
    webserver_hosts = frozenset(
        name for name in webserver_notified if settings[name]["php_enable_webserver"]
    )
    fpm_direct = changed_hosts(configure_report, php.FPM_NOTIFY_NAMES)
    fpm_hosts = frozenset(
        name
        for name in (webserver_hosts | fpm_direct)
        if settings[name]["php_enable_php_fpm"] and settings[name]["php_fpm_state"] == "started"
    )
    handler_plans = {
        host.name: php.handler_plan(settings[host.name], distributions[host.name])
        for host in targets
    }
    handler_report = await execute(php.build_handler(handler_plans, webserver_hosts, fpm_hosts))
    return {
        "reports": reports,
        "settings": settings,
        "distributions": distributions,
        "configure_plans": configure_plans,
        "package_changed": package_changed,
        "webserver_hosts": webserver_hosts,
        "fpm_hosts": fpm_hosts,
        "handler_report": handler_report,
    }


@pytest.fixture
def rig() -> tuple[list[dict[str, Any]], dict[str, SimulatedHost]]:
    """An inventory plus the persistent simulated hosts it materializes into."""
    inventory = make_inventory()
    return inventory, make_hosts(inventory)


def changed_entries(result: dict[str, Any]) -> list[Any]:
    """Every changed log entry across the whole sequence, handler deploy included."""
    return [entry for report in result["reports"] for entry in report.log if entry.changed]


# --- the first pass really deploys the role ----------------------------------


async def test_first_pass_installs_and_configures_php(rig):
    inventory, hosts = rig
    result = await migrate(inventory, hosts)
    host = hosts["ubuntu"]
    settings = result["settings"]["ubuntu"]

    # The version table selected 8.3 and the packages installed; cache refreshed.
    assert result["distributions"]["ubuntu"]["major"] == 24
    assert "php8.3-cli" in host.apt_packages
    assert "php8.3-fpm" in host.apt_packages
    assert "php8.3-apcu" in host.apt_packages
    assert host.apt_update_stamp is not None
    assert result["package_changed"] == frozenset({"ubuntu"})

    # The packaged ini files are gone -- both the symlinks and the regular
    # files -- while their mods-available targets survive (rm on a link does
    # not touch its target).
    for ext in EXT_PATHS:
        assert f"{ext}/{APC_FILE}" not in host.links
        assert f"{ext}/{OPCACHE_FILE}" not in host.links
    assert f"{E_FPM}/{APC_FILE}" in host.remote_files  # re-installed by deploy 3
    assert host.remote_files[f"{MODS}/apcu.ini"] == b"extension=apcu.so\n"

    # The stray sweep: direct unmanaged configs removed, dotfile + nested kept,
    # and the commented line removed too (the broader grep match, a deviation).
    assert STRAY_APCU not in host.remote_files
    assert STRAY_OPCACHE not in host.remote_files
    assert COMMENTED_APCU not in host.remote_files
    assert HIDDEN_APCU in host.remote_files
    assert NESTED_APCU in host.remote_files
    assert result["configure_plans"]["ubuntu"]["stray_apcu"] == (
        STRAY_APCU,
        COMMENTED_APCU,
    )
    assert result["configure_plans"]["ubuntu"]["stray_opcache"] == (STRAY_OPCACHE,)

    # The managed php.ini in every conf path, with the tuned memory limit.
    for conf in CONF_PATHS:
        content = host.remote_files[f"{conf}/php.ini"].decode()
        assert "memory_limit = 512M" in content
        assert content == php.render_php_ini(settings)
        assert host.file_stats[f"{conf}/php.ini"]["mode"] == 644
        assert host.file_stats[f"{conf}/php.ini"]["user"] == "root"

    # The role's extension ini files, with the conditional blacklist line and
    # the verbatim (non-boolified) opcache.enable value.
    for ext in EXT_PATHS:
        apc = host.remote_files[f"{ext}/{APC_FILE}"].decode()
        assert apc == php.render_apc_ini(settings)
        assert "extension=apcu.so" in apc
        opcache = host.remote_files[f"{ext}/{OPCACHE_FILE}"].decode()
        assert "opcache.enable=1\n" in opcache
        assert "opcache.blacklist_filename=/etc/php/opcache-blacklist\n" in opcache

    # The pool directory was absent and is now created; the www pool rendered
    # with the falsy-default pm.max_requests (0 -> 500) and the status path.
    assert POOL_DIR in host.directory_stats
    pool = host.remote_files[f"{POOL_DIR}/www.conf"].decode()
    assert pool.startswith("; Ansible managed\n")
    assert "[www]" in pool
    assert "listen = 127.0.0.1:9000" in pool
    assert "pm.max_requests = 500" in pool
    assert "pm.status_path = /status" in pool
    assert "user = www-data" in pool

    # The FPM service is started and enabled at boot.
    assert host.services["php8.3-fpm.service"] == "active"
    assert "php8.3-fpm.service" in host.enabled_services

    # Both handlers fired: the webserver restart and the chained FPM restart.
    assert result["webserver_hosts"] == frozenset({"ubuntu"})
    assert result["fpm_hosts"] == frozenset({"ubuntu"})
    handler_names = {entry.name for entry in result["handler_report"].log if entry.changed}
    assert "Restart apache2 (handler)" in handler_names
    assert "Restart php8.3-fpm (handler)" in handler_names


async def test_non_ubuntu_host_is_never_touched(rig):
    inventory, hosts = rig
    result = await migrate(inventory, hosts)

    skipped = hosts["web1"]
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}
    assert skipped.links == {}
    assert skipped.services == {}
    for report in result["reports"]:
        for entry in report.log:
            if entry.host == "web1":
                assert entry.skipped
                assert not entry.changed


async def test_handler_honours_the_fpm_handler_state(rig):
    """php_fpm_handler_state selects the chained FPM handler's verb."""
    inventory = make_inventory(php_fpm_handler_state="reloaded")
    hosts = make_hosts(inventory)
    result = await migrate(inventory, hosts)

    assert result["fpm_hosts"] == frozenset({"ubuntu"})
    handler_names = {entry.name for entry in result["handler_report"].log if entry.changed}
    assert "Reload php8.3-fpm (handler)" in handler_names
    assert "Restart php8.3-fpm (handler)" not in handler_names


# --- the second pass is a no-op ----------------------------------------------


async def test_second_pass_changes_nothing_and_queues_no_handler(rig):
    inventory, hosts = rig
    first = await migrate(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    # Bridge the two simulation limits before pass 2 reads the seams again.
    for host in hosts.values():
        if php.is_ubuntu(host):
            settle(host)
    second = await migrate(inventory, hosts)

    assert changed_entries(second) == []
    assert second["package_changed"] == frozenset()
    assert second["webserver_hosts"] == frozenset()
    assert second["fpm_hosts"] == frozenset()
    # The handler deploy still runs, but it holds no request for any host.
    assert not any(entry.changed for entry in second["handler_report"].log)
    # The pool dir is now present, so the create request is omitted entirely.
    assert second["configure_plans"]["ubuntu"]["pool_dir"] is None
    assert second["configure_plans"]["ubuntu"]["stray_apcu"] == ()
    assert second["configure_plans"]["ubuntu"]["stray_opcache"] == ()


async def test_dry_preview_mutates_nothing(rig):
    """``dry=True`` stops after prepare, so it must not touch the target."""
    inventory, hosts = rig
    host = hosts["ubuntu"]
    before_files = dict(host.remote_files)
    before_links = dict(host.links)
    before_services = dict(host.services)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    settings = php.role_settings(host)
    distribution = host.linux_distribution
    install_plans = {host.name: php.install_plan(settings, distribution)}
    configure_plans = {host.name: php.configure_plan(settings, distribution, (), (), False)}
    await run(inventory, php.build_install(install_plans), host_factory=factory, dry=True)
    await run(inventory, php.build_configure(configure_plans), host_factory=factory, dry=True)

    assert host.remote_files == before_files
    assert host.links == before_links
    assert host.services == before_services
    assert host.apt_packages == {}


# --- the FPM-disabled default path -------------------------------------------


async def test_fpm_disabled_skips_pools_service_and_fpm_handler():
    """The role default (php_enable_php_fpm false) manages no pool or FPM service."""
    inventory = make_inventory(php_enable_php_fpm=False)
    hosts = make_hosts(inventory)
    result = await migrate(inventory, hosts)
    host = hosts["ubuntu"]
    plan = result["configure_plans"]["ubuntu"]

    assert plan["pools"] == ()
    assert plan["fpm_service"] is None
    assert f"{POOL_DIR}/www.conf" not in host.remote_files
    assert "php8.3-fpm.service" not in host.services
    # The webserver handler still fires (the package install changed), but the
    # chained FPM handler is gated off.
    assert result["webserver_hosts"] == frozenset({"ubuntu"})
    assert result["fpm_hosts"] == frozenset()
    handler_names = {entry.name for entry in result["handler_report"].log if entry.changed}
    assert "Restart apache2 (handler)" in handler_names
    assert not any("php-fpm" in name for name in handler_names)

    for target in hosts.values():
        if php.is_ubuntu(target):
            settle(target)
    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []
    assert second["webserver_hosts"] == frozenset()


# --- the pure helpers, unit-tested directly -----------------------------------


def test_stray_configs_recovers_find_semantics():
    hits = (
        f"{E_CLI}/stray.ini",  # direct, unmanaged -> kept
        f"{E_CLI}/.hidden.ini",  # dotfile -> dropped
        f"{E_CLI}/nested/old.ini",  # not a direct child -> dropped
        f"{E_CLI}/{APC_FILE}",  # the managed filename -> dropped
        f"{E_APACHE}/other.ini",  # direct child of another ext path -> kept
    )
    assert php.stray_configs(hits, EXT_PATHS, APC_FILE) == (
        f"{E_APACHE}/other.ini",
        f"{E_CLI}/stray.ini",
    )


def test_render_opcache_ini_keeps_the_raw_enable_value():
    settings = php.role_settings(
        SimulatedHost.from_inventory(make_inventory(php_opcache_blacklist_filename="")[0])
    )
    # The default "1" renders verbatim, not a boolified True.
    assert "opcache.enable=1\n" in php.render_opcache_ini(settings)
    assert "opcache.blacklist_filename" not in php.render_opcache_ini(settings)
    settings["php_opcache_enable"] = True
    assert "opcache.enable=True\n" in php.render_opcache_ini(settings)
    settings["php_opcache_blacklist_filename"] = "/etc/blacklist"
    assert php.render_opcache_ini(settings).endswith("opcache.blacklist_filename=/etc/blacklist\n")


def test_render_www_conf_falsy_defaults_and_status_path():
    settings = php.role_settings(
        SimulatedHost.from_inventory(make_inventory(php_fpm_pm_status_path="")[0])
    )
    rv = php.resolved_vars(settings, {"name": "Ubuntu", "major": 24})
    pool = settings["php_fpm_pools"][0]
    # The role default php_fpm_pm_max_requests is 0 (falsy) -> Jinja2 default 500.
    assert pool["pool_pm_max_requests"] == 0
    content = php.render_www_conf(pool, settings, rv)
    assert "pm.max_requests = 500" in content
    assert "pm.status_path" not in content  # the default status path is empty
    pool["pool_pm_status_path"] = "/status"
    assert "pm.status_path = /status" in php.render_www_conf(pool, settings, rv)


@pytest.mark.parametrize(
    ("major", "version", "json"),
    [(20, "7.4", True), (22, "8.1", False), (24, "8.3", False), (99, "8.2", False)],
)
def test_resolved_vars_version_table(major, version, json):
    settings = php.role_settings(SimulatedHost.from_inventory(make_inventory()[0]))
    rv = php.resolved_vars(settings, {"name": "Ubuntu", "major": major})
    assert rv["version"] == version
    assert f"php{version}-cli" in rv["packages"]
    # PHP < 8.0 appends the json package, exactly as main.yml does.
    assert (f"php{version}-json" in rv["packages"]) is json
    assert rv["fpm_daemon"] == f"php{version}-fpm"


def test_resolved_vars_honours_the_version_override():
    settings = php.role_settings(
        SimulatedHost.from_inventory(make_inventory(php_default_version_debian="8.4")[0])
    )
    rv = php.resolved_vars(settings, {"name": "Ubuntu", "major": 24})
    assert rv["version"] == "8.4"
    assert "php8.4-cli" in rv["packages"]


def test_role_settings_rejects_invalid_values():
    entry = make_inventory(php_packages_state="newest")[0]
    with pytest.raises(ValueError, match="php_packages_state"):
        php.role_settings(SimulatedHost.from_inventory(entry))
    entry = make_inventory(php_enable_apc="maybe")[0]
    with pytest.raises(ValueError, match="php_enable_apc"):
        php.role_settings(SimulatedHost.from_inventory(entry))
    entry = make_inventory(php_packages_extra="php-redis")[0]
    with pytest.raises(TypeError, match="php_packages_extra"):
        php.role_settings(SimulatedHost.from_inventory(entry))


def test_pools_reject_a_custom_template():
    entry = make_inventory(
        php_fpm_pools=[
            {"pool_name": "app", "pool_listen": "127.0.0.1:9001", "pool_template": "custom.j2"}
        ]
    )[0]
    with pytest.raises(ValueError, match="www.conf.j2"):
        php.role_settings(SimulatedHost.from_inventory(entry))