# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end test for the PHP Xdebug Ansible-role migration example.

``examples/roles/ansible-role-php-xdebug/ansible-role-php-xdebug.py`` connects
for real when run as a script, so the suite never executes its ``main()``. What
it *can* do offline is replay the same orchestration against ``SimulatedHost``:
the example is imported as a module, its factories are fed snapshots gathered
from simulated state, the build runs through the action transport seam (#16),
and every deploy goes through the real driver (prepare + apply, plus a
``dry=True`` preview).

The load-bearing assertion is the migration's idempotence claim: run the whole
setup -> build action -> install -> handler sequence, then run it *again*
against the same persistent hosts, and the second pass must report
``changed=False`` everywhere, fetch no tarball, run no build and queue no
handler.

One between-pass bridge is needed, a *simulation* limit rather than a library
bug: the ``Shell`` action seam answers the build from ``shell_results`` but
mutates no file store, so ``bridge_built_so()`` seeds the module ``make``
produces (``remote_files`` + ``existing_files`` + ``file_stats``) right after the
action -- ``files.copy`` validates ``src`` in *both* phases, so the install
deploy cannot run until the built module exists.

No ``settle()`` bridge is needed (unlike the sibling php test): the unarchive
``creates`` gate converges because ``extract_archive`` adds the directory member
to ``existing_files`` itself; the copy's rename-parent check reads ``Directory``
(``directory_stats``), which ``make_directory`` writes; and the copy's sha1 gate
converges because ``copy_path`` duplicates the content bytes to the target.

Seeding choices, not bridges: ``linux_distribution`` (Ubuntu 24 -> PHP 8.3), the
``archive_members`` listing keyed on the URL spool (a directory member
``xdebug-3.3.2/`` is what makes ``creates`` converge), the ``shell_results``
record keyed by the build's exact ``sudo`` rendering, and the ``/root`` workspace
plus the three extension ``conf.d`` directories as the ``geerlingguy.php``
dependency's post-install state (this program assumes PHP is already present).
The module path ``/usr/lib/php7/modules`` is *not* seeded -- deploy 1 creates it.
"""

import importlib.util
import posixpath
from pathlib import Path
from typing import Any

import pytest

from reemote import (
    CommandError,
    FileExists,
    LinuxDistribution,
    Shell,
    changed_hosts,
    gather_action,
    gather_fact,
    run,
)
from reemote.modules.files import files as files_module
from reemote.modules.files.files import _unarchive_spool_path
from tests.simulated_host import SimulatedHost

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = (
    REPO_ROOT
    / "examples"
    / "roles"
    / "ansible-role-php-xdebug"
    / "ansible-role-php-xdebug.py"
)


def load_example() -> Any:
    """Import the migration example by path (its name is not a valid identifier)."""
    spec = importlib.util.spec_from_file_location(
        "ansible_role_php_xdebug_example", EXAMPLE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


xdebug = load_example()

# Ubuntu 24.04 -> the sibling php migration's version table selects PHP 8.3.
# The module path and conf filename are the role's vars/Debian.yml fallbacks.
VERSION = "8.3"
XVERSION = "3.3.2"
WORKSPACE = "/root"
MODULE_PATH = "/usr/lib/php7/modules"
CONFIG_FILENAME = "20-xdebug.ini"
SRC_DIR = f"{WORKSPACE}/xdebug-{XVERSION}"
BUILT_SO = f"{SRC_DIR}/modules/xdebug.so"
INSTALLED_SO = f"{MODULE_PATH}/xdebug-{XVERSION}.so"
URL = f"https://xdebug.org/files/xdebug-{XVERSION}.tgz"
SPOOL = _unarchive_spool_path(URL)
C_FPM = f"/etc/php/{VERSION}/fpm"
C_APACHE = f"/etc/php/{VERSION}/apache2"
C_CLI = f"/etc/php/{VERSION}/cli"
E_FPM = f"{C_FPM}/conf.d"
E_APACHE = f"{C_APACHE}/conf.d"
E_CLI = f"{C_CLI}/conf.d"
EXT_PATHS = (E_FPM, E_APACHE, E_CLI)
INI_DESTS = tuple(f"{ext}/{CONFIG_FILENAME}" for ext in EXT_PATHS)
CLI_INI = f"{E_CLI}/{CONFIG_FILENAME}"
MODS = f"/etc/php/{VERSION}/mods-available"

# A gzip-magic binary body (never valid UTF-8) and a fake ELF module: the
# control-side fetch and the build's product, both opaque to the simulation.
FAKE_TARBALL = b"\x1f\x8b\x08\x00\xff\xfe\x00binary-tarball"
FAKE_SO = b"\x7fELF\x02\x01\x01\x00fake-xdebug-module"

#: The exact shell the sudo=True build action renders (no privilege section in
#: the inventory, so a bare ``sudo`` prefix on the explicit ``sh -c`` chain) --
#: the shell_results seam key. Pinned by test_build_command_renders_the_shell_key.
BUILD_SHELL = "sudo sh -c 'cd /root/xdebug-3.3.2 && phpize && ./configure && make'"

#: templates/xdebug.ini.j2 rendered with the role defaults, spelled out from the
#: role's source so the render assertion compares against the template itself.
EXPECTED_INI = (
    "[XDebug]\n"
    'zend_extension="/usr/lib/php7/modules/xdebug-3.3.2.so"\n'
    "\n"
    "xdebug.coverage_enable=1\n"
    "xdebug.default_enable=1\n"
    "\n"
    "xdebug.remote_enable=false\n"
    "xdebug.remote_connect_back=false\n"
    "xdebug.remote_host=localhost\n"
    "xdebug.remote_port=9000\n"
    "xdebug.remote_log=/tmp/xdebug.log\n"
    "xdebug.remote_autostart=false\n"
    "\n"
    'xdebug.idekey="sublime.xdebug"\n'
    "\n"
    "xdebug.max_nesting_level=256\n"
)


@pytest.fixture
def canned_fetch(monkeypatch):
    """Replace the control-side tarball fetch with a canned-bytes stub.

    ``files.unarchive`` looks ``_fetch_url_bytes`` up as a module global at call
    time, so patching the module attribute redirects the fetch. Returns the list
    of URLs the stub was called with, so a test can assert the fetch happened --
    and that a converged second pass fetches nothing (the ``creates`` gate).
    """
    fetched: list[str] = []

    def fake_fetch(url: str) -> bytes:
        fetched.append(url)
        return FAKE_TARBALL

    monkeypatch.setattr(files_module, "_fetch_url_bytes", fake_fetch)
    return fetched


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride as extra keys -- reemote's ``host_vars`` -- so
    ``role_settings`` reads them straight off ``host.extra``. The base entry
    enables PHP-FPM so the chained ``restart php-fpm`` handler is exercised; the
    role default (``php_enable_php_fpm`` false) is covered by a dedicated test.
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


def seed(host: SimulatedHost) -> None:
    """Pre-state: a stock Ubuntu 24.04 machine with PHP already installed.

    The extension conf.d directories are the geerlingguy.php dependency's
    post-install state (this program does not install PHP). The tarball member
    listing and the build's canned shell answer ride the unarchive and action
    seams, keyed exactly as the operations render them.
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
        WORKSPACE: {"mode": 700, "user": "root", "group": "root"},
        **{ext: {"mode": 755, "user": "root", "group": "root"} for ext in EXT_PATHS},
    }
    host.existing_files |= set(host.directory_stats)
    host.services = {}
    host.enabled_services = set()

    settings = xdebug.role_settings(host)
    rv = xdebug.resolved_vars(settings, host.linux_distribution)
    # The directory member (trailing /) is what makes the creates gate converge:
    # extract_archive adds it to existing_files, so pass 2's FileExists sees it.
    base = posixpath.basename(rv["src_dir"])
    host.archive_members = {
        _unarchive_spool_path(rv["tarball_url"]): (
            base + "/",
            base + "/config.m4",
            base + "/php_xdebug.h",
        )
    }
    # The build action's exact sudo rendering is the shell_results seam key.
    host.shell_results["sudo " + xdebug.build_command(rv)] = {
        "exit_status": 0,
        "stdout": "",
        "stderr": "",
    }


def bridge_built_so(host: SimulatedHost, rv: dict[str, Any]) -> None:
    """Seed the module ``make`` produces, after the build action.

    The Shell action seam runs the build but mutates no file store, so the
    built ``xdebug.so`` must be planted by hand before ``files.copy`` validates
    its ``src`` (in both phases). A simulation limit, not a library bug.
    """
    path = rv["built_so"]
    host.remote_files[path] = FAKE_SO
    host.existing_files.add(path)
    host.file_stats[path] = {
        "user": "root",
        "group": "root",
        "mode": 755,
        "size": len(FAKE_SO),
    }


def make_hosts(inventory: list[dict[str, Any]]) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if xdebug.is_ubuntu(host):
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

    The sequence mirrors ``main()`` barrier for barrier -- the distribution read
    before deploy 1, the ``FileExists`` build gate after the extraction, the
    build action between the setup and install deploys, the ``changed_hosts``
    notification read before the handler -- but reads the simulated hosts
    instead of opening SSH connections.
    """
    targets = [host for host in hosts.values() if xdebug.is_ubuntu(host)]
    settings = {host.name: xdebug.role_settings(host) for host in targets}
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
    resolved = {
        host.name: xdebug.resolved_vars(settings[host.name], distributions[host.name])
        for host in targets
    }

    # Deploy 1: build-essential, the module directory, the source extraction.
    setup_report = await execute(xdebug.build_setup(resolved))

    # The build's creates: gate, read AFTER the extraction.
    need_build: set[str] = set()
    try:
        for host in targets:
            rv = resolved[host.name]
            exists = (await gather_fact([host], FileExists, rv["built_so"]))[host.name]
            if not exists:
                need_build.add(host.name)
    finally:
        for host in targets:
            await host.aclose()
    need_build_set = frozenset(need_build)

    # The build ACTION between deploys, on the need_build hosts only.
    if need_build_set:
        groups: dict[str, list[Any]] = {}
        for host in targets:
            if host.name in need_build_set:
                cmd = xdebug.build_command(resolved[host.name])
                groups.setdefault(cmd, []).append(host)
        for cmd, group in groups.items():
            results = await gather_action(group, Shell, command=cmd, sudo=True)
            for name, result in results.items():
                if result["exit_status"] != 0:
                    raise CommandError(
                        name, cmd, result["exit_status"], result["stderr"]
                    )
        # Bridge the module the build produced (the Shell seam stores nothing).
        for host in targets:
            if host.name in need_build_set:
                bridge_built_so(host, resolved[host.name])

    # Deploy 2: the versioned module copy, the ini installs, the CLI disable.
    install_report = await execute(xdebug.build_install(resolved))

    # The handlers: changed_hosts over the notifying names, then the when guards.
    webserver_notified = changed_hosts(install_report, xdebug.WEBSERVER_NOTIFY_NAMES)
    webserver_hosts = frozenset(
        name for name in webserver_notified if settings[name]["php_enable_webserver"]
    )
    fpm_direct = changed_hosts(install_report, xdebug.FPM_NOTIFY_NAMES)
    fpm_hosts = frozenset(
        name
        for name in (webserver_hosts | fpm_direct)
        if settings[name]["php_enable_php_fpm"]
        and settings[name]["php_fpm_state"] == "started"
    )
    handler_report = await execute(
        xdebug.build_handler(resolved, webserver_hosts, fpm_hosts)
    )
    return {
        "reports": reports,
        "settings": settings,
        "distributions": distributions,
        "resolved": resolved,
        "need_build": need_build_set,
        "webserver_hosts": webserver_hosts,
        "fpm_hosts": fpm_hosts,
        "setup_report": setup_report,
        "install_report": install_report,
        "handler_report": handler_report,
    }


@pytest.fixture
def rig() -> tuple[list[dict[str, Any]], dict[str, SimulatedHost]]:
    """An inventory plus the persistent simulated hosts it materializes into."""
    inventory = make_inventory()
    return inventory, make_hosts(inventory)


def changed_entries(result: dict[str, Any]) -> list[Any]:
    """Every changed log entry across the whole sequence, handler deploy included."""
    return [
        entry for report in result["reports"] for entry in report.log if entry.changed
    ]


# --- the first pass really builds and installs Xdebug ------------------------


async def test_first_pass_builds_and_installs_xdebug(rig, canned_fetch):
    inventory, hosts = rig
    result = await migrate(inventory, hosts)
    host = hosts["ubuntu"]
    rv = result["resolved"]["ubuntu"]

    # The version table selected 8.3; build-essential installed (no cache refresh).
    assert result["distributions"]["ubuntu"]["major"] == 24
    assert "build-essential" in host.apt_packages
    assert host.apt_update_stamp is None

    # The tarball was fetched control-side and extracted; the creates path landed
    # in both stores the gate and the Directory fact read.
    assert canned_fetch.count(URL) > 0
    assert SRC_DIR in host.existing_files
    assert SRC_DIR in host.directory_stats
    assert SPOOL not in host.remote_files  # the spool was cleaned up

    # The module directory was created by deploy 1 (the copy's rename parent).
    assert MODULE_PATH in host.directory_stats
    assert host.directory_stats[MODULE_PATH]["mode"] == 755

    # The build ran exactly once, on the need_build host.
    assert result["need_build"] == frozenset({"ubuntu"})
    assert host.ran_shells == [BUILD_SHELL]

    # The versioned module copy: renamed, root:root 0644, source kept in place.
    assert host.remote_files[INSTALLED_SO] == FAKE_SO
    assert host.file_stats[INSTALLED_SO]["mode"] == 644
    assert host.file_stats[INSTALLED_SO]["user"] == "root"
    assert host.file_stats[INSTALLED_SO]["group"] == "root"
    assert BUILT_SO in host.remote_files  # files.copy keeps the source

    # The rendered xdebug.ini in every extension conf path, root:root 0644.
    assert rv["ini_dests"] == INI_DESTS
    for dest in INI_DESTS:
        assert host.remote_files[dest].decode() == EXPECTED_INI
        assert host.file_stats[dest]["mode"] == 644
        assert host.file_stats[dest]["user"] == "root"

    # Both chained handlers fired: the webserver restart and the FPM restart.
    assert result["webserver_hosts"] == frozenset({"ubuntu"})
    assert result["fpm_hosts"] == frozenset({"ubuntu"})
    handler_names = {
        entry.name for entry in result["handler_report"].log if entry.changed
    }
    assert "Restart apache2 (handler)" in handler_names
    assert "Restart php8.3-fpm (handler)" in handler_names
    assert host.services["apache2.service"] == "active"
    assert host.services["php8.3-fpm.service"] == "active"


async def test_non_ubuntu_host_is_never_touched(rig):
    inventory, hosts = rig
    result = await migrate(inventory, hosts)

    skipped = hosts["web1"]
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}
    assert skipped.directory_stats == {}
    assert skipped.services == {}
    assert skipped.ran_shells == []
    for report in result["reports"]:
        for entry in report.log:
            if entry.host == "web1":
                assert entry.skipped
                assert not entry.changed


# --- the second pass is a no-op ----------------------------------------------


async def test_second_pass_changes_nothing_and_queues_no_handler(rig, canned_fetch):
    inventory, hosts = rig
    first = await migrate(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"
    fetches_after_first = len(canned_fetch)
    assert fetches_after_first > 0

    # No settle() bridge: every seam pass 2 reads is maintained by the
    # simulation's own mutators (see the module docstring). The built module
    # persists from pass 1's bridge, so need_build converges to empty.
    second = await migrate(inventory, hosts)

    assert changed_entries(second) == []
    assert second["need_build"] == frozenset()
    assert second["webserver_hosts"] == frozenset()
    assert second["fpm_hosts"] == frozenset()
    assert not any(entry.changed for entry in second["handler_report"].log)
    # The creates gate skipped every fetch, and the build ran exactly once total.
    assert len(canned_fetch) == fetches_after_first
    assert hosts["ubuntu"].ran_shells == [BUILD_SHELL]


async def test_dry_preview_mutates_nothing(rig):
    """``dry=True`` stops after prepare, so it must not touch the target."""
    inventory, hosts = rig
    host = hosts["ubuntu"]
    before_dirs = dict(host.directory_stats)
    before_files = set(host.existing_files)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    settings = xdebug.role_settings(host)
    resolved = {host.name: xdebug.resolved_vars(settings, host.linux_distribution)}
    await run(inventory, xdebug.build_setup(resolved), host_factory=factory, dry=True)

    assert host.remote_files == {}
    assert host.apt_packages == {}
    assert host.directory_stats == before_dirs
    assert host.existing_files == before_files
    assert host.services == {}
    assert host.ran_shells == []


# --- the CLI-disable branch --------------------------------------------------


async def test_cli_disable_removes_the_cli_ini_and_skips_the_cli_write():
    inventory = make_inventory(php_xdebug_cli_disable=True)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    # The stock CLI conf.d entry is a symlink into mods-available.
    _symlink(host, CLI_INI, f"{MODS}/xdebug.ini")

    result = await migrate(inventory, hosts)
    rv = result["resolved"]["ubuntu"]

    # The CLI path is excluded from the ini installs; the other two are written.
    assert rv["ini_dests"] == (
        f"{E_FPM}/{CONFIG_FILENAME}",
        f"{E_APACHE}/{CONFIG_FILENAME}",
    )
    assert CLI_INI not in rv["ini_dests"]
    assert f"{E_FPM}/{CONFIG_FILENAME}" in host.remote_files
    assert f"{E_APACHE}/{CONFIG_FILENAME}" in host.remote_files
    # The stock CLI symlink was removed (force=True removes the link itself).
    assert CLI_INI not in host.links
    assert CLI_INI not in host.remote_files

    # The removal notifies both handlers, so they still fire.
    assert result["webserver_hosts"] == frozenset({"ubuntu"})
    assert result["fpm_hosts"] == frozenset({"ubuntu"})

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []
    assert second["webserver_hosts"] == frozenset()


# --- the FPM handler gates ---------------------------------------------------


async def test_fpm_disabled_skips_the_fpm_handler():
    """The role default (php_enable_php_fpm false) fires only the webserver handler."""
    inventory = make_inventory(php_enable_php_fpm=False)
    hosts = make_hosts(inventory)
    result = await migrate(inventory, hosts)

    assert result["webserver_hosts"] == frozenset({"ubuntu"})
    assert result["fpm_hosts"] == frozenset()
    handler_names = {
        entry.name for entry in result["handler_report"].log if entry.changed
    }
    assert "Restart apache2 (handler)" in handler_names
    assert not any("php-fpm" in name for name in handler_names)
    assert "php8.3-fpm.service" not in hosts["ubuntu"].services

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []
    assert second["webserver_hosts"] == frozenset()


async def test_fpm_handler_state_verbs():
    """Each php_fpm_handler_state selects the chained FPM handler's verb."""
    for state, name in (
        ("restarted", "Restart php8.3-fpm (handler)"),
        ("reloaded", "Reload php8.3-fpm (handler)"),
        ("started", "Start php8.3-fpm (handler)"),
    ):
        inventory = make_inventory(php_fpm_handler_state=state)
        hosts = make_hosts(inventory)
        result = await migrate(inventory, hosts)
        assert result["fpm_hosts"] == frozenset({"ubuntu"})
        handler_names = {
            entry.name for entry in result["handler_report"].log if entry.changed
        }
        assert name in handler_names


async def test_fpm_state_stopped_gates_off_the_fpm_handler():
    """The handler fires only when php_fpm_state == 'started', even if FPM enabled."""
    inventory = make_inventory(php_fpm_state="stopped")
    hosts = make_hosts(inventory)
    result = await migrate(inventory, hosts)

    assert result["webserver_hosts"] == frozenset({"ubuntu"})
    assert result["fpm_hosts"] == frozenset()


async def test_build_failure_raises_command_error(rig):
    """A non-zero build exit is data in the Result, re-raised typed."""
    inventory, hosts = rig
    hosts["ubuntu"].shell_results[BUILD_SHELL] = {
        "exit_status": 1,
        "stdout": "",
        "stderr": "make: *** [Makefile:123: xdebug.lo] Error 1\n",
    }
    with pytest.raises(CommandError, match="phpize"):
        await migrate(inventory, hosts)


# --- the pure helpers, unit-tested directly -----------------------------------


def test_render_xdebug_ini_matches_the_template_byte_for_byte():
    settings = xdebug.role_settings(SimulatedHost.from_inventory(make_inventory()[0]))
    assert xdebug.render_xdebug_ini(settings, MODULE_PATH, XVERSION) == EXPECTED_INI


def test_render_keeps_the_raw_values_not_boolified():
    settings = xdebug.role_settings(SimulatedHost.from_inventory(make_inventory()[0]))
    rendered = xdebug.render_xdebug_ini(settings, MODULE_PATH, XVERSION)
    # The role's int 1 and string "false" interpolate verbatim.
    assert "xdebug.coverage_enable=1\n" in rendered
    assert "xdebug.remote_enable=false\n" in rendered
    # A Python True would render "True", not Ansible's "true" -- the raw contract.
    settings["php_xdebug_remote_enable"] = True
    assert "xdebug.remote_enable=True\n" in xdebug.render_xdebug_ini(
        settings, MODULE_PATH, XVERSION
    )


@pytest.mark.parametrize(
    ("major", "version"),
    [(20, "7.4"), (22, "8.1"), (24, "8.3"), (99, "8.2")],
)
def test_resolved_vars_version_table(major, version):
    settings = xdebug.role_settings(SimulatedHost.from_inventory(make_inventory()[0]))
    rv = xdebug.resolved_vars(settings, {"name": "Ubuntu", "major": major})
    assert rv["version"] == version
    assert rv["fpm_daemon"] == f"php{version}-fpm"
    assert rv["ext_paths"] == tuple(
        f"/etc/php/{version}/{s}/conf.d" for s in ("fpm", "apache2", "cli")
    )


def test_resolved_vars_debian_fallbacks():
    settings = xdebug.role_settings(SimulatedHost.from_inventory(make_inventory()[0]))
    rv = xdebug.resolved_vars(settings, {"name": "Ubuntu", "major": 24})
    assert rv["module_path"] == MODULE_PATH
    assert rv["config_filename"] == CONFIG_FILENAME
    assert rv["installed_so"] == INSTALLED_SO
    assert rv["src_dir"] == SRC_DIR
    assert rv["built_so"] == BUILT_SO
    assert rv["tarball_url"] == URL
    assert rv["ini_dests"] == INI_DESTS
    assert rv["cli_ini"] == CLI_INI
    assert rv["webserver_daemon"] == "apache2"
    assert rv["ini_content"] == EXPECTED_INI


def test_resolved_vars_honours_overrides():
    entry = make_inventory(
        php_xdebug_module_path="/usr/lib/php/20230831",
        php_xdebug_config_filename="30-xdebug.ini",
        php_default_version_debian="8.4",
        workspace="/opt",
        php_xdebug_version="3.4.0",
    )[0]
    settings = xdebug.role_settings(SimulatedHost.from_inventory(entry))
    rv = xdebug.resolved_vars(settings, {"name": "Ubuntu", "major": 24})
    assert rv["version"] == "8.4"
    assert rv["module_path"] == "/usr/lib/php/20230831"
    assert rv["installed_so"] == "/usr/lib/php/20230831/xdebug-3.4.0.so"
    assert rv["src_dir"] == "/opt/xdebug-3.4.0"
    assert rv["tarball_url"] == "https://xdebug.org/files/xdebug-3.4.0.tgz"
    assert rv["config_filename"] == "30-xdebug.ini"
    assert rv["fpm_daemon"] == "php8.4-fpm"


def test_resolved_vars_custom_ext_paths():
    entry = make_inventory(php_extension_conf_paths=["/custom/conf.d"])[0]
    settings = xdebug.role_settings(SimulatedHost.from_inventory(entry))
    rv = xdebug.resolved_vars(settings, {"name": "Ubuntu", "major": 24})
    assert rv["ext_paths"] == ("/custom/conf.d",)
    assert rv["ini_dests"] == (f"/custom/conf.d/{CONFIG_FILENAME}",)


def test_resolved_vars_cli_disable_excludes_the_cli_path():
    entry = make_inventory(php_xdebug_cli_disable=True)[0]
    settings = xdebug.role_settings(SimulatedHost.from_inventory(entry))
    rv = xdebug.resolved_vars(settings, {"name": "Ubuntu", "major": 24})
    assert rv["cli_disable"] is True
    assert rv["ini_dests"] == (
        f"{E_FPM}/{CONFIG_FILENAME}",
        f"{E_APACHE}/{CONFIG_FILENAME}",
    )
    # The CLI removal target is still resolved from php_version, not ext_paths.
    assert rv["cli_ini"] == CLI_INI


def test_build_command_renders_the_shell_key():
    settings = xdebug.role_settings(SimulatedHost.from_inventory(make_inventory()[0]))
    rv = xdebug.resolved_vars(settings, {"name": "Ubuntu", "major": 24})
    cmd = xdebug.build_command(rv)
    assert cmd == "sh -c 'cd /root/xdebug-3.3.2 && phpize && ./configure && make'"
    # The sudo=True action renders "sudo " + command verbatim -- the seam key.
    assert "sudo " + cmd == BUILD_SHELL


@pytest.mark.parametrize(
    ("overrides", "error", "match"),
    [
        ({"php_fpm_state": "bogus"}, ValueError, "php_fpm_state"),
        ({"php_fpm_handler_state": "bogus"}, ValueError, "php_fpm_handler_state"),
        ({"php_xdebug_cli_disable": "maybe"}, ValueError, "php_xdebug_cli_disable"),
        ({"php_enable_php_fpm": "maybe"}, ValueError, "php_enable_php_fpm"),
        ({"php_xdebug_version": "3.x"}, ValueError, "php_xdebug_version"),
        ({"workspace": ""}, ValueError, "workspace"),
        (
            {"php_extension_conf_paths": "notalist"},
            TypeError,
            "php_extension_conf_paths",
        ),
        ({"php_xdebug_module_path": 42}, TypeError, "php_xdebug_module_path"),
    ],
)
def test_role_settings_rejects_invalid_values(overrides, error, match):
    host = SimulatedHost.from_inventory(make_inventory(**overrides)[0])
    with pytest.raises(error, match=match):
        xdebug.role_settings(host)
