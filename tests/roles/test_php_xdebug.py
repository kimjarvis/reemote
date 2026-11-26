# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``php_xdebug`` role (design doc section 17.7, T7).

The role is library code now, so it is imported (``from reemote.roles import
php_xdebug``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, php_xdebug(xdebug_v, php_v), host_factory=...)``. The old
example's harness is gone with it -- no ``gather_fact`` between four ``run()``
calls, no ``plans`` dicts keyed by host name, no ``compose`` of one handler per
host. The dependency is a ``yield from`` inside the body, so one pass of one deploy
covers php's waves and xdebug's.

Four assertions carry this migration's claims:

* **the return-value channel** (§15's falsifier). The ini destinations and the two
  handler daemons follow the *dependency's* resolution: set
  the ``php`` role's ``default_version_debian`` and everything xdebug writes moves,
  :attr:`~reemote.roles.php.PhpOutputs.version` and
  :attr:`~reemote.roles.php.PhpOutputs.extension_conf_paths` are what it reads. The
  module carries no Ubuntu version table and no distribution read of its own, which
  :func:`test_the_role_carries_no_copy_of_php_resolution` checks mechanically.
* **one checkpoint, two name sets** (§18.7). Exactly one ``CheckpointRequest``
  belongs to xdebug, and both ``changed_hosts`` reads come out of *its* outcome.
* **php's notifications stay php's.** A checkpoint reads only the wave it closes, so
  on a host where only php drifted, the tree shows one webserver restart -- php's --
  and xdebug's barrier line says ``0 hosts notified``. That separation is the whole
  reason reports are not merged.
* **convergence**. A second pass against the same persistent hosts changes nothing,
  fetches no tarball, runs no build and queues no handler.

Seeding choices, none of them a bridge:

* ``archive_members`` is keyed on the URL spool path ``files.unarchive`` stages to,
  and the listing carries the ``xdebug-3.3.2/`` **directory** member: that is what
  makes the operation's in-flight ``creates`` gate converge, so pass 2 never fetches.
* ``shell_results`` is keyed by the build's verbatim ``sudo`` rendering, and the
  module ``make`` produces is planted by :class:`BuildHost`, a ``SimulatedHost``
  subclass whose ``_run_shell`` writes the file the action would have built. The seam
  itself mutates no store; doing it *in* the seam is what lets the whole role run as
  one stream -- the old suite's ``bridge_built_so()`` between two ``run()`` calls has
  no analogue here, because nothing runs between the barriers.
* the workspace directory is seeded (``/root`` exists on a real host), the module
  path is not: deploy 1's ``files.directory`` creates it.
* the control-side tarball fetch is stubbed by the ``canned_fetch`` fixture, asked
  for explicitly by every test that runs the role: a test that never reaches the
  extraction should not be quietly carrying a network stub.
"""

import ast
import posixpath
import sys
from typing import Any

import pytest
from pydantic import ValidationError

from reemote import CommandError, resolve_vars, run_waves
from reemote.handlers import changed_hosts
from reemote.modules.files import files as files_module
from reemote.modules.files.files import _unarchive_spool_path
from reemote.roles import PhpVars, PhpXdebugVars, php_xdebug
from reemote.roles.php import PhpOutputs, resolve_php
from reemote.roles.php_xdebug import (
    DEBIAN_CONFIG_FILENAME,
    DEBIAN_MODULE_PATH,
    FPM_NOTIFY_NAMES,
    TARBALL_URL,
    WEBSERVER_NOTIFY_NAMES,
    build_command,
    render_xdebug_ini,
    resolve_xdebug,
)
from reemote.rolevars import RoleVarsError
from tests.simulated_host import SimulatedHost

# Ubuntu 24.04, so php's own table selects PHP 8.3 -- the version every path below
# is spelled from. Nothing here re-derives it: it comes from resolve_php.
DISTRO = {"name": "Ubuntu", "family": "Debian", "major": 24, "minor": 0, "release_meta": {}}
VERSION = "8.3"
XVERSION = "3.3.2"
WORKSPACE = "/root"
MODULE_PATH = DEBIAN_MODULE_PATH
CONFIG_FILENAME = DEBIAN_CONFIG_FILENAME
SRC_DIR = f"{WORKSPACE}/xdebug-{XVERSION}"
BUILT_SO = f"{SRC_DIR}/modules/xdebug.so"
INSTALLED_SO = f"{MODULE_PATH}/xdebug-{XVERSION}.so"
URL = TARBALL_URL.format(version=XVERSION)
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
PHP_INI_CLI = f"{C_CLI}/php.ini"

# A gzip-magic binary body (never valid UTF-8) and a fake ELF module: the
# control-side fetch and the build's product, both opaque to the simulation.
FAKE_TARBALL = b"\x1f\x8b\x08\x00\xff\xfe\x00binary-tarball"
FAKE_SO = b"\x7fELF\x02\x01\x01\x00fake-xdebug-module"

#: The exact shell the ``sudo=True`` build action renders (the inventory carries no
#: privilege section, so a bare ``sudo`` on the explicit ``sh -c`` chain). Pinned by
#: test_build_command_renders_the_shell_key.
BUILD_SHELL = "sudo sh -c 'cd /root/xdebug-3.3.2 && phpize && ./configure && make'"

#: templates/xdebug.ini.j2 with the role defaults, spelled out from the template so
#: the render is compared against the role's own text, not against itself.
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

APACHE_HANDLER = "Restart apache2 (handler)"
FPM_HANDLER = f"Restart php{VERSION}-fpm (handler)"
XDEBUG_CHECKPOINT = "xdebug install done"
#: The four checkpoints of the composed stream: php's three, then xdebug's one.
CHECKPOINT_NAMES = ["install done", "cleanup done", "configure done", XDEBUG_CHECKPOINT]

#: The role module, for the source-level falsifier check. ``from reemote.roles
#: import php_xdebug`` binds the *function* (the package export shadows the
#: submodule), so the module object is taken from ``sys.modules``.
MODULE = sys.modules[resolve_xdebug.__module__]


# --- fixtures and rig --------------------------------------------------------


@pytest.fixture
def canned_fetch(monkeypatch):
    """Replace the control-side tarball fetch with a canned-bytes stub.

    ``files.unarchive`` looks ``_fetch_url_bytes`` up as a module global at call
    time, so patching the module attribute redirects the fetch. Returns the list of
    URLs the stub was called with, so a test can assert the fetch happened -- and
    that a converged second pass fetches nothing, which is the ``creates`` gate.
    """
    fetched: list[str] = []

    def fake_fetch(url: str) -> bytes:
        fetched.append(url)
        return FAKE_TARBALL

    monkeypatch.setattr(files_module, "_fetch_url_bytes", fake_fetch)
    return fetched


def make_inventory(**roles: dict[str, Any]) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride on the entry's ``roles`` mapping, one nested dict per
    role name written with the models' bare field names. Each keyword argument
    names a role and updates that role's mapping, so a test changes a variable
    exactly the way an operator does -- and ``php``/``php_xdebug`` resolve
    independently, the claim the old ``nested_prefixes`` escape stood in for. The
    base entry enables PHP-FPM, so the chained ``restart php-fpm`` handler is
    exercised; the role default (FPM off) has its own test.
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
        "roles": {
            "php": {"enable_php_fpm": True},
            "php_xdebug": {},
        },
    }
    for role, variables in roles.items():
        entry["roles"].setdefault(role, {}).update(variables)
    return [
        entry,
        {
            "host": "web1",
            "family": "RedHat",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "centos",
        },
    ]


def resolution_for(entry: dict[str, Any]) -> Any:
    """The role's own :class:`PhpXdebugResolution` for one inventory entry.

    Built the way the body builds it -- php's resolver first, then xdebug's -- so the
    seed keys (the spool path, the build shell) are the values the real run will use
    rather than a second copy of the path arithmetic. Each role reads only its own
    mapping in the entry's ``roles`` dict.
    """
    pv = PhpVars.model_validate(entry["roles"]["php"])
    hv = PhpXdebugVars.model_validate(entry["roles"]["php_xdebug"])
    rv_php = resolve_php(pv, dict(DISTRO))
    php_out = PhpOutputs(
        version=rv_php.version, extension_conf_paths=rv_php.ext_paths
    )
    return resolve_xdebug(hv, php_out, pv)


def _regular_file(host: SimulatedHost, path: str, content: bytes, mode: int = 644) -> None:
    """Seed a regular file across the three stores its facts read."""
    host.remote_files[path] = content
    host.existing_files.add(path)
    host.file_stats[path] = {
        "user": "root",
        "group": "root",
        "mode": mode,
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


class BuildHost(SimulatedHost):
    """A simulated host whose build action leaves the module it claims to build.

    The ``Shell`` seam answers from ``shell_results`` and mutates no file store, so a
    role that builds a binary and then copies it needs the product planted. Under
    ``run_waves`` the copy is prepared in the wave *after* the action barrier applied,
    so planting it in the seam is enough: no test code runs between the two, which is
    the point of the composed stream. A simulation limit, not a library bug -- a real
    ``make`` writes the file.

    ``built_so`` is set per host by :func:`make_hosts` from that host's own resolved
    vars, so a test that overrides the ``version`` variable still converges.
    """

    #: Overridden per host; the default is the role default's build product.
    built_so = BUILT_SO

    async def _run_shell(self, shell: str) -> Any:
        result = await super()._run_shell(shell)
        if result.exit_status == 0 and "phpize && ./configure && make" in shell:
            _regular_file(self, self.built_so, FAKE_SO, mode=755)
        return result


def seed(host: BuildHost, entry: dict[str, Any], *, build_exit: int = 0) -> None:
    """Pre-state: a stock Ubuntu 24.04 machine with neither PHP nor Xdebug present.

    PHP is *not* installed, because this role now installs it: the composed stream runs
    the dependency first, so the pre-state is the same one the php suite seeds. The
    workspace exists (``/root`` does on a real host); the module path does not, since
    the setup wave creates it.
    """
    host.linux_distribution = dict(DISTRO)
    host.apt_packages = {}
    host.apt_update_stamp = None
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}
    host.links = {}
    host.link_stats = {}
    host.directory_stats = {WORKSPACE: {"mode": 700, "user": "root", "group": "root"}}
    host.existing_files |= set(host.directory_stats)
    host.services = {}
    host.enabled_services = set()
    host.grep_files = {}

    rv = resolution_for(entry)
    host.built_so = rv.built_so
    # The directory member (trailing slash) is what makes the unarchive `creates` gate
    # converge: the extraction registers it, so pass 2 answers the gate and never fetches.
    base = posixpath.basename(rv.src_dir)
    host.archive_members = {
        _unarchive_spool_path(rv.tarball_url): (
            base + "/",
            f"{base}/config.m4",
            f"{base}/php_xdebug.h",
            f"{base}/src/xdebug.c",
        )
    }
    host.shell_results["sudo " + build_command(rv)] = {
        "exit_status": build_exit,
        "stdout": "",
        "stderr": "" if build_exit == 0 else "make: *** [Makefile:60: all] Error 1",
    }


def make_hosts(
    inventory: list[dict[str, Any]], *, build_exit: int = 0
) -> dict[str, BuildHost]:
    """Materialize the inventory into persistent simulated hosts, seeded target.

    Only the Ubuntu entry is seeded, so the RedHat host keeps its stock empty stores
    and an accidental request on it shows up as untouched state.
    """
    hosts: dict[str, BuildHost] = {}
    for entry in inventory:
        host = BuildHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            seed(host, entry, build_exit=build_exit)
        hosts[host.name] = host
    return hosts


async def run_role(
    inventory: list[dict[str, Any]],
    hosts: dict[str, BuildHost],
    *,
    dry: bool = False,
) -> Any:
    """Resolve both roles' variables and run one wave pass against persistent hosts.

    Each role binds only its own mapping in the entry's ``roles`` dict, so the
    child's variables are never the parent's typos -- the property the old
    ``nested_prefixes`` parameter existed to emulate. The same ``Host`` objects come
    back for every entry, so state survives from one pass to the next, which is the
    whole point of the second run.
    """
    php_v = resolve_vars(PhpVars, "php", inventory)
    xdebug_v = resolve_vars(PhpXdebugVars, "php_xdebug", inventory)

    def factory(entry: dict[str, Any]) -> BuildHost:
        return hosts[entry["host"]]

    report, _hosts = await run_waves(
        inventory, php_xdebug(xdebug_v, php_v), host_factory=factory, dry=dry
    )
    return report


@pytest.fixture
def rig() -> tuple[list[dict[str, Any]], dict[str, BuildHost]]:
    """An inventory plus the persistent simulated hosts it materializes into."""
    inventory = make_inventory()
    return inventory, make_hosts(inventory)


# --- report readers ----------------------------------------------------------


def changed_entries(report: Any) -> list[Any]:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def entries_named(report: Any, name: str) -> list[Any]:
    """Every log entry with *name*, on any host."""
    return [entry for entry in report.merged().log if entry.name == name]


def entry_named(report: Any, name: str, host: str = "ubuntu") -> Any:
    """The one log entry with *name* on *host*."""
    return next(
        entry
        for entry in report.merged().log
        if entry.name == name and entry.host == host
    )


def host_records(report: Any, host: str = "ubuntu") -> list[Any]:
    """Every record (request step or barrier) attributed to *host*."""
    return [record for record in report.records if record.host == host]


def checkpoint_records(report: Any, host: str = "ubuntu") -> list[Any]:
    """The checkpoint records of one pass, in wave order."""
    return [
        record
        for record in host_records(report, host)
        if record.barrier is not None
        and type(record.barrier).__name__ == "CheckpointRequest"
    ]


def barrier_names(report: Any, host: str = "ubuntu") -> list[str]:
    """The label of every barrier *host* parked on, in wave order.

    Mirrors what ``report.tree()`` prints: a checkpoint or action barrier is labelled
    by its own ``name``, a fact barrier carries none and is labelled by the fact class.
    """
    labels = []
    for record in host_records(report, host):
        barrier = record.barrier
        if barrier is None:
            continue
        labels.append(getattr(barrier, "name", "") or barrier.fact_cls.__name__)
    return labels


def wave_of(report: Any, name: str, host: str = "ubuntu") -> int:
    """The wave one request name was applied in."""
    return next(
        record.wave
        for record in host_records(report, host)
        if record.planned is not None and record.planned.request.name == name
    )


def request_kwargs(report: Any, name: str, host: str = "ubuntu") -> dict:
    """The forwarded keyword arguments of the one planned request called *name*."""
    record = next(
        record
        for record in host_records(report, host)
        if record.planned is not None and record.planned.request.name == name
    )
    return record.planned.request.kwargs


def requests_named(report: Any, name: str) -> list[Any]:
    """Every planned request called *name*, on any host, in record order."""
    return [
        record.planned.request
        for record in report.records
        if record.planned is not None and record.planned.request.name == name
    ]


def handler_names(report: Any, host: str = "ubuntu") -> list[str]:
    """The handler entries of one pass, in the order they ran."""
    return [
        entry.name
        for entry in report.merged().log
        if entry.host == host and entry.name.endswith("(handler)")
    ]


def text(host: BuildHost, path: str) -> str:
    """What a simulated file holds, decoded."""
    return host.remote_files[path].decode("utf-8")


# --- the composed first pass -------------------------------------------------


async def test_first_pass_runs_php_then_builds_and_installs(rig, canned_fetch):
    inventory, hosts = rig
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # The dependency ran first: php's own waves are in this stream, not in a
    # separate call -- the version-specific packages are installed and php.ini is
    # written before xdebug's setup requests.
    assert f"php{VERSION}-dev" in host.apt_packages
    assert PHP_INI_CLI in host.remote_files
    assert wave_of(report, "Ensure PHP packages are installed") < wave_of(
        report, "Ensure dependencies for building from source are installed (Debian)."
    )

    # The toolchain, with no cache refresh (the role's apt task sets none).
    assert "build-essential" in host.apt_packages

    # The tarball was fetched control-side, extracted, and the spool cleaned up.
    assert canned_fetch == [URL]
    assert SRC_DIR in host.existing_files
    assert SRC_DIR in host.directory_stats
    assert _unarchive_spool_path(URL) not in host.remote_files

    # The module directory is created by this role's setup wave (php never makes it).
    assert MODULE_PATH in host.directory_stats
    assert host.directory_stats[MODULE_PATH]["mode"] == 755

    # The build ran once, through the action seam, on the host whose module was absent.
    assert host.ran_shells == [BUILD_SHELL]

    # The versioned module copy: renamed, root:root 0644, source kept in place.
    assert host.remote_files[INSTALLED_SO] == FAKE_SO
    assert host.file_stats[INSTALLED_SO]["mode"] == 644
    assert host.file_stats[INSTALLED_SO]["user"] == "root"
    assert BUILT_SO in host.remote_files

    # The rendered ini, in every extension conf path php resolved.
    for dest in INI_DESTS:
        assert text(host, dest) == EXPECTED_INI
        assert host.file_stats[dest]["mode"] == 644

    # Both chained handlers fired, and the units are up.
    assert handler_names(report).count(APACHE_HANDLER) >= 1
    assert FPM_HANDLER in handler_names(report)
    assert host.services["apache2.service"] == "active"
    assert host.services[f"php{VERSION}-fpm.service"] == "active"


async def test_the_dependency_barrier_sequence_is_php_then_xdebug(rig, canned_fetch):
    """Every barrier of the composed stream, in the order the body yields them."""
    inventory, hosts = rig
    report = await run_role(inventory, hosts)

    assert barrier_names(report) == [
        "LinuxDistribution",
        "install done",
        "cleanup done",
        "FindInFiles",
        "FindInFiles",
        "FindInFiles",
        "FindInFiles",
        "FindInFiles",
        "FindInFiles",
        "FileExists",
        "configure done",
        "FileExists",
        "build xdebug",
        XDEBUG_CHECKPOINT,
    ]


async def test_non_ubuntu_host_is_never_touched(rig, canned_fetch):
    """php's family guard skips the host before xdebug's body can reach it."""
    inventory, hosts = rig
    report = await run_role(inventory, hosts)

    skipped = hosts["web1"]
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}
    assert skipped.directory_stats == {}
    assert skipped.services == {}
    assert skipped.ran_shells == []
    assert host_records(report, "web1") == []


# --- the return-value channel (§15's falsifier) ------------------------------


def code_names(source: str) -> set[str]:
    """Every identifier the module's *code* mentions, docstrings and strings excluded.

    The falsifier is about what the role reads, and its prose necessarily names the
    variables the dependency owns -- that is what section 15's write-up is for -- so
    the check looks at the syntax tree rather than at raw bytes.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_the_role_carries_no_copy_of_php_resolution():
    """No version table, no distribution read, no hand-inherited conf paths.

    This is the mechanical half of design doc section 15's falsifier: the migration
    exists to *delete* the copied Ubuntu table and the reconstruction of
    ``php_version`` / ``php_extension_conf_paths``. If either name came back, the
    return-value channel would be decorative.
    """
    with open(MODULE.__file__, encoding="utf-8") as handle:
        source = handle.read()
    names = code_names(source)

    assert "UBUNTU_PHP_VERSIONS" not in names
    assert "DEBIAN_DEFAULT_PHP_VERSION" not in names
    assert "LinuxDistribution" not in names
    assert "php_default_version_debian" not in names
    assert "php_extension_conf_paths" not in names
    # And the channel itself is what supplies them.
    assert "php_out.version" in source
    assert "php_out.extension_conf_paths" in source


async def test_the_php_version_variable_moves_every_xdebug_path(canned_fetch):
    """The ``php`` role's ``default_version_debian`` moves everything; xdebug follows.

    The old example carried its own copy of the version table, so an operator setting
    that variable moved php's files and left xdebug's behind. Here nothing has to be
    kept in sync: the ini destinations, the CLI removal and the FPM daemon name all
    come from php's resolution.
    """
    inventory = make_inventory(php={"default_version_debian": "8.1"})
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    e_fpm = "/etc/php/8.1/fpm/conf.d"
    e_apache = "/etc/php/8.1/apache2/conf.d"
    dests = [
        r.kwargs["dest"]
        for r in requests_named(report, "Copy xdebug ini into main extension config folder.")
    ]
    assert dests == [
        f"{e_fpm}/{CONFIG_FILENAME}",
        f"{e_apache}/{CONFIG_FILENAME}",
        "/etc/php/8.1/cli/conf.d/20-xdebug.ini",
    ]
    assert f"{e_fpm}/{CONFIG_FILENAME}" in host.remote_files
    # The daemon name is derived from the same version, through php's own helper.
    assert f"Restart php{VERSION}-fpm (handler)" not in handler_names(report)
    assert "Restart php8.1-fpm (handler)" in handler_names(report)


async def test_the_extension_conf_paths_override_is_inherited(canned_fetch):
    """An operator's ``extension_conf_paths`` redirect is honoured by xdebug too."""
    inventory = make_inventory(
        php={"extension_conf_paths": ["/srv/php/ext/fpm", "/srv/php/ext/cli"]}
    )
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    dests = [
        r.kwargs["dest"]
        for r in requests_named(report, "Copy xdebug ini into main extension config folder.")
    ]
    assert dests == [
        f"/srv/php/ext/fpm/{CONFIG_FILENAME}",
        f"/srv/php/ext/cli/{CONFIG_FILENAME}",
    ]


# --- one checkpoint, two name sets ------------------------------------------


async def test_xdebug_has_one_checkpoint_whose_report_feeds_both_sets(rig, canned_fetch):
    """The webserver and FPM sets are read out of the *same* checkpoint outcome.

    Design doc section 18.7's point: every notifying task lives in one deploy, so a
    second checkpoint would read an empty wave. Both name sets resolve to this host
    from one report, and the ini writes -- not the module move -- are what carry the
    FPM notification.
    """
    inventory, hosts = rig
    report = await run_role(inventory, hosts)

    checkpoints = checkpoint_records(report)
    assert [record.barrier.name for record in checkpoints] == CHECKPOINT_NAMES

    xdebug_report = checkpoints[-1].outcome
    assert type(xdebug_report).__name__ == "ApplyReport"
    assert changed_hosts(xdebug_report, WEBSERVER_NOTIFY_NAMES) == frozenset({"ubuntu"})
    assert changed_hosts(xdebug_report, FPM_NOTIFY_NAMES) == frozenset({"ubuntu"})
    # The module move is in the webserver set only -- a host with FPM disabled
    # restarts the webserver and nothing else.
    assert "Move Xdebug module into place." in {
        entry.name for entry in xdebug_report.log if entry.changed
    }
    assert "Move Xdebug module into place." not in FPM_NOTIFY_NAMES


async def test_only_php_drifting_restarts_the_webserver_once(rig, canned_fetch):
    """php's notifications are not borrowed, so xdebug's barrier reports nobody.

    With everything converged, drift one file *php* manages. php's own handler wave
    restarts the webserver; xdebug's checkpoint sees only its own unchanged wave, so
    it queues no handler of its own. Folding
    :attr:`~reemote.roles.php.PhpOutputs.webserver_notified` into the set below would
    produce a second restart for a reason Xdebug had nothing to do with.
    """
    inventory, hosts = rig
    await run_role(inventory, hosts)

    host = hosts["ubuntu"]
    del host.remote_files[PHP_INI_CLI]
    host.file_stats[PHP_INI_CLI] = False
    host.existing_files.discard(PHP_INI_CLI)

    report = await run_role(inventory, hosts)
    xdebug_checkpoint = checkpoint_records(report)[-1]

    # Both restarts are php's own, in php's single handler wave: the ini write that
    # drifted is php's, and the FPM restart rides the webserver chain.
    assert handler_names(report) == [APACHE_HANDLER, FPM_HANDLER]
    handler_entries = [
        entry
        for entry in report.merged().log
        if entry.host == "ubuntu" and entry.name.endswith("(handler)")
    ]
    handler_waves = {
        next(record.wave for record in report.records if record.log is entry)
        for entry in handler_entries
    }
    assert len(handler_waves) == 1, "xdebug queued no handler of its own"

    assert changed_hosts(xdebug_checkpoint.outcome, WEBSERVER_NOTIFY_NAMES) == frozenset()
    assert changed_hosts(xdebug_checkpoint.outcome, FPM_NOTIFY_NAMES) == frozenset()
    assert f"barrier: {XDEBUG_CHECKPOINT} → 0 hosts notified" in report.tree()
    # The build did not run again: the planted module is still there.
    assert host.ran_shells == [BUILD_SHELL]
    assert canned_fetch == [URL]


async def test_a_fresh_host_restarts_the_webserver_twice(rig, canned_fetch):
    """Two handler waves, two restarts -- no de-duplication, and none wanted.

    php restarts because its configuration changed; xdebug restarts because its ini
    write changed. The roles' handler sets are separate by design, and the driver has
    no dedupe, so the same unit is restarted twice in one pass -- in two different
    waves. A converged pass queues neither.
    """
    inventory, hosts = rig
    report = await run_role(inventory, hosts)

    restarts = entries_named(report, APACHE_HANDLER)
    assert len(restarts) == 2
    waves = {
        next(r.wave for r in report.records if r.log is entry)
        for entry in restarts
    }
    assert len(waves) == 2


# --- convergence -------------------------------------------------------------


async def test_second_pass_changes_nothing_and_queues_no_handler(rig, canned_fetch):
    inventory, hosts = rig
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    second = await run_role(inventory, hosts)

    assert changed_entries(second) == []
    assert canned_fetch == [URL], "the creates gate must skip the second fetch"
    assert hosts["ubuntu"].ran_shells == [BUILD_SHELL], "the build runs exactly once"
    assert handler_names(second) == []
    assert f"barrier: {XDEBUG_CHECKPOINT} → 0 hosts notified" in second.tree()
    # The handler waves vanished from the tree; the barrier sequence is otherwise
    # identical, minus the action the gate skipped.
    assert XDEBUG_CHECKPOINT in second.tree()
    assert "ActionRequest" not in [
        type(record.barrier).__name__ for record in host_records(second)
    ]


async def test_dry_preview_stops_at_the_dependency_barrier(rig):
    """``dry=True`` parks at the first barrier -- which is php's, not xdebug's."""
    inventory, hosts = rig
    host = hosts["ubuntu"]

    report = await run_role(inventory, hosts, dry=True)

    assert report.tree() == DRY_PREVIEW_TREE
    assert host.remote_files == {}
    assert host.apt_packages == {}
    assert host.ran_shells == []
    assert host.directory_stats == {WORKSPACE: {"mode": 700, "user": "root", "group": "root"}}


async def test_disabling_the_role_skips_the_dependency_too():
    """``php_xdebug: {enable: false}`` returns before the dependency's first barrier.

    The knob is new (the role has no analogue), and its position is the whole claim: a
    disabled host pays for none of php's reads either, so it appears in no wave.
    """
    inventory = make_inventory(php_xdebug={"enable": False})
    hosts = make_hosts(inventory)

    report = await run_role(inventory, hosts)

    assert hosts["ubuntu"].apt_packages == {}
    assert host_records(report, "ubuntu") == []


# --- the build gate and its failure -----------------------------------------


async def test_build_is_gated_on_the_module_the_action_produces(rig, canned_fetch):
    """The ``FileExists`` read branches the body -- and comes after the extraction.

    Design doc section 6.2: reading a barrier result and branching on it is the model
    working as intended. The gate closes the *setup* wave, so its answer already
    reflects the extraction that ran in that same wave, and the build action lands in
    the wave after it.
    """
    inventory, hosts = rig
    report = await run_role(inventory, hosts)

    records = host_records(report)
    setup_index = next(
        index
        for index, record in enumerate(records)
        if record.planned is not None
        and record.planned.request.name
        == "Ensure dependencies for building from source are installed (Debian)."
    )
    gate_indices = [
        index
        for index, record in enumerate(records)
        if type(record.barrier).__name__ == "FactRequest"
        and record.barrier.args == (BUILT_SO,)
    ]
    assert len(gate_indices) == 1
    gate_index = gate_indices[0]
    assert gate_index > setup_index, "the gate is read after the extraction is issued"
    assert records[gate_index].wave == records[setup_index].wave
    assert records[gate_index].outcome is False, "the module is absent on a fresh host"

    action_index = next(
        index
        for index, record in enumerate(records)
        if type(record.barrier).__name__ == "ActionRequest"
    )
    assert records[action_index].wave == records[gate_index].wave + 1
    copy_index = next(
        index
        for index, record in enumerate(records)
        if record.planned is not None
        and record.planned.request.name == "Move Xdebug module into place."
    )
    assert records[copy_index].wave == records[action_index].wave + 1


async def test_build_failure_raises_command_error(rig, canned_fetch):
    """A barrier's non-zero exit is data, so the body turns it into the error."""
    inventory, hosts = rig
    hosts["ubuntu"].shell_results[BUILD_SHELL]["exit_status"] = 1

    with pytest.raises(CommandError, match="phpize"):
        await run_role(inventory, hosts)

    host = hosts["ubuntu"]
    assert host.ran_shells == [BUILD_SHELL]
    # Nothing after the build barrier was reached: the ini was never written.
    assert INSTALLED_SO not in host.remote_files
    assert INI_DESTS[0] not in host.remote_files


def test_build_command_renders_the_shell_key():
    """The wrapped ``sh -c`` chain is the exact ``shell_results`` seam key."""
    rv = resolution_for(make_inventory()[0])

    assert build_command(rv) == f"sh -c 'cd {rv.src_dir} && phpize && ./configure && make'"
    assert "sudo " + build_command(rv) == BUILD_SHELL


# --- the gates the dependency owns ------------------------------------------


async def test_cli_disable_removes_the_cli_ini_and_skips_the_cli_write(canned_fetch):
    inventory = make_inventory(php_xdebug={"cli_disable": True})
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    # The stock CLI conf.d entry is a symlink into mods-available.
    _symlink(host, CLI_INI, f"{MODS}/xdebug.ini")

    report = await run_role(inventory, hosts)

    dests = [
        r.kwargs["dest"]
        for r in requests_named(report, "Copy xdebug ini into main extension config folder.")
    ]
    assert dests == [f"{E_FPM}/{CONFIG_FILENAME}", f"{E_APACHE}/{CONFIG_FILENAME}"]
    assert f"{E_FPM}/{CONFIG_FILENAME}" in host.remote_files
    assert CLI_INI not in host.links
    assert CLI_INI not in host.remote_files
    # The removal notifies both handlers, so they still fire.
    assert APACHE_HANDLER in handler_names(report)
    assert FPM_HANDLER in handler_names(report)

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert handler_names(second) == []


async def test_fpm_disabled_skips_the_fpm_handler(canned_fetch):
    """``enable_php_fpm: false`` on the ``php`` role is the *dependency's*, read here."""
    inventory = make_inventory(php={"enable_php_fpm": False})
    hosts = make_hosts(inventory)

    report = await run_role(inventory, hosts)

    assert APACHE_HANDLER in handler_names(report)
    assert FPM_HANDLER not in handler_names(report)
    assert hosts["ubuntu"].services.get(f"php{VERSION}-fpm.service") is None


async def test_webserver_disabled_skips_only_the_webserver_handler(canned_fetch):
    """A host can reach the FPM handler through the chain without restarting apache."""
    inventory = make_inventory(php={"enable_webserver": False})
    hosts = make_hosts(inventory)

    report = await run_role(inventory, hosts)

    assert APACHE_HANDLER not in handler_names(report)
    assert FPM_HANDLER in handler_names(report)


async def test_fpm_handler_state_selects_the_verb(canned_fetch):
    """``fpm_handler_state`` -> the handler's verb, including ``started``->start."""
    inventory = make_inventory(php={"fpm_handler_state": "reloaded"})
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert "Reload php8.3-fpm (handler)" in handler_names(report)
    assert FPM_HANDLER not in handler_names(report)

    inventory = make_inventory(php={"fpm_handler_state": "started"})
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert "Start php8.3-fpm (handler)" in handler_names(report)


async def test_fpm_state_stopped_gates_off_the_fpm_handler(canned_fetch):
    inventory = make_inventory(php={"fpm_state": "stopped"})
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert APACHE_HANDLER in handler_names(report)
    assert FPM_HANDLER not in handler_names(report)


# --- variables and the role-name nesting -----------------------------------


async def test_module_path_and_config_filename_overrides(canned_fetch):
    """The two ``vars/Debian.yml`` fallbacks are overridable, and every path moves."""
    inventory = make_inventory(
        php_xdebug={
            "module_path": "/usr/lib/php/20230831",
            "config_filename": "99-xdebug.ini",
        }
    )
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    installed = f"/usr/lib/php/20230831/xdebug-{XVERSION}.so"
    assert request_kwargs(report, "Move Xdebug module into place.")["dest"] == installed
    assert installed in host.remote_files
    assert text(host, f"{E_FPM}/99-xdebug.ini") == EXPECTED_INI.replace(
        "/usr/lib/php7/modules", "/usr/lib/php/20230831"
    )


async def test_xdebug_version_moves_the_source_tree_and_the_copy_target(canned_fetch):
    inventory = make_inventory(php_xdebug={"version": "3.2.0"})
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    assert canned_fetch == ["https://xdebug.org/files/xdebug-3.2.0.tgz"]
    built = "/root/xdebug-3.2.0/modules/xdebug.so"
    assert request_kwargs(report, "Untar Xdebug.")["creates"] == "/root/xdebug-3.2.0"
    assert request_kwargs(report, "Move Xdebug module into place.")["src"] == built
    assert host.ran_shells == [
        "sudo sh -c 'cd /root/xdebug-3.2.0 && phpize && ./configure && make'"
    ]


def test_role_names_keep_the_two_variable_sets_apart():
    """The child's variables are the parent's neighbour, not the parent's typo.

    The old example's ``php_`` / ``php_xdebug_`` prefix pair needed the
    ``nested_prefixes`` escape for this; the entry's ``roles`` mapping gives each
    role its own dict, so neither role ever reads the other's keys -- design doc
    section 8.2, superseded by the nested layout.
    """
    inventory = make_inventory(php_xdebug={"version": "3.2.0"})

    resolved = resolve_vars(PhpVars, "php", inventory)

    assert resolved["ubuntu"].enable_php_fpm is True
    assert resolved["web1"].enable_php_fpm is False
    xv = resolve_vars(PhpXdebugVars, "php_xdebug", inventory)
    assert xv["ubuntu"].version == "3.2.0"


def test_the_parents_own_typo_still_raises():
    """The nesting is not an escape hatch: a real misspelling is still an error."""
    inventory = make_inventory(php={"webserver_damon": "apache2"})

    with pytest.raises(RoleVarsError, match="webserver_damon"):
        resolve_vars(PhpVars, "php", inventory)


def test_the_ini_render_is_the_template_verbatim():
    assert render_xdebug_ini(PhpXdebugVars(), MODULE_PATH, XVERSION) == EXPECTED_INI
    assert render_xdebug_ini(PhpXdebugVars(), MODULE_PATH, XVERSION).endswith("\n")


def test_render_values_keep_the_roles_raw_text_forms():
    """Numbers arrive as Jinja would render them; a JSON bool is refused.

    The role defaults ``php_xdebug_coverage_enable: 1`` and ships
    ``php_xdebug_remote_enable: "false"`` as a *string*. ``coerce_numbers_to_str``
    renders the integer as ``1``; a declared ``bool`` field would have rendered
    ``False`` where the template writes ``false``.
    """
    v = PhpXdebugVars(coverage_enable=1, max_nesting_level=256, remote_port=9000)

    assert v.coverage_enable == "1"
    assert v.max_nesting_level == "256"
    assert v.remote_port == "9000"
    assert "xdebug.remote_port=9000\n" in render_xdebug_ini(v, MODULE_PATH, XVERSION)

    with pytest.raises(ValidationError):
        PhpXdebugVars(remote_enable=True)


def test_bad_versions_and_workspaces_are_refused_before_any_host():
    with pytest.raises(ValidationError, match="dotted digits"):
        PhpXdebugVars(version="3.3.2rc1")
    with pytest.raises(ValidationError, match="non-empty"):
        PhpXdebugVars(workspace="")


def test_resolve_xdebug_reads_only_what_php_publishes():
    """The resolution's php-derived fields come from the outputs, not from vars."""
    php_out = PhpOutputs(
        version="8.1",
        extension_conf_paths=("/etc/php/8.1/fpm/conf.d", "/etc/php/8.1/cli/conf.d"),
    )
    rv = resolve_xdebug(PhpXdebugVars(cli_disable=True), php_out, PhpVars())

    assert rv.php_version == "8.1"
    assert rv.ext_paths == ("/etc/php/8.1/fpm/conf.d", "/etc/php/8.1/cli/conf.d")
    assert rv.ini_dests == (f"/etc/php/8.1/fpm/conf.d/{DEBIAN_CONFIG_FILENAME}",)
    assert rv.cli_ini == f"/etc/php/8.1/cli/conf.d/{DEBIAN_CONFIG_FILENAME}"
    assert rv.built_so == BUILT_SO
    assert rv.installed_so == f"{DEBIAN_MODULE_PATH}/xdebug-{XVERSION}.so"
    assert rv.webserver_daemon == "apache2"
    assert rv.fpm_daemon == "php8.1-fpm"
    assert rv.fpm_handler_action == "restarted"


def test_the_dependency_daemon_fallbacks_come_from_php():
    """``daemon_names`` is php's, so xdebug never repeats ``php<version>-fpm``."""
    from reemote.roles.php import daemon_names

    pv = PhpVars()
    assert daemon_names(pv, "8.3") == ("apache2", "php8.3-fpm")
    assert daemon_names(PhpVars(webserver_daemon="nginx", fpm_daemon="php-fpm"), "8.3") == (
        "nginx",
        "php-fpm",
    )

    rv = resolve_xdebug(PhpXdebugVars(), PhpOutputs(version="8.1", extension_conf_paths=()), pv)
    assert (rv.webserver_daemon, rv.fpm_daemon) == ("apache2", "php8.1-fpm")


# --- the wave tree (design doc section 7.2) ----------------------------------

# Pinned from a real simulated run. The composed stream is php's twelve waves plus
# xdebug's: the setup requests ride php's handler wave (no barrier separates them),
# then the build gate, the build action, the install wave with its one checkpoint, and
# xdebug's own handler wave. A converged pass drops both handler waves and the action.
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
    ubuntu: Delete APCu configuration file if this role will provide one [no change: file /etc/php/8.3/fpm/conf.d/20-apcu.ini does not exist]
    ubuntu: Delete APCu configuration file if this role will provide one [no change: file /etc/php/8.3/apache2/conf.d/20-apcu.ini does not exist]
    ubuntu: Delete APCu configuration file if this role will provide one [no change: file /etc/php/8.3/cli/conf.d/20-apcu.ini does not exist]
    ubuntu: Delete OpCache configuration file if this role will provide one [no change: file /etc/php/8.3/fpm/conf.d/10-opcache.ini does not exist]
    ubuntu: Delete OpCache configuration file if this role will provide one [no change: file /etc/php/8.3/apache2/conf.d/10-opcache.ini does not exist]
    ubuntu: Delete OpCache configuration file if this role will provide one [no change: file /etc/php/8.3/cli/conf.d/10-opcache.ini does not exist]
    barrier: cleanup done → 0 hosts notified
  wave 3
    barrier: FindInFiles → []
  wave 4
    barrier: FindInFiles → []
  wave 5
    barrier: FindInFiles → []
  wave 6
    barrier: FindInFiles → []
  wave 7
    barrier: FindInFiles → []
  wave 8
    barrier: FindInFiles → []
  wave 9
    barrier: FileExists → False
  wave 10
    ubuntu: Ensure configuration directories exist [changed: /etc/php/8.3/fpm missing, will create]
        $ sudo mkdir -p /etc/php/8.3/fpm
        $ sudo chmod 755 /etc/php/8.3/fpm
    ubuntu: Ensure configuration directories exist [changed: /etc/php/8.3/apache2 missing, will create]
        $ sudo mkdir -p /etc/php/8.3/apache2
        $ sudo chmod 755 /etc/php/8.3/apache2
    ubuntu: Ensure configuration directories exist [changed: /etc/php/8.3/cli missing, will create]
        $ sudo mkdir -p /etc/php/8.3/cli
        $ sudo chmod 755 /etc/php/8.3/cli
    ubuntu: Ensure configuration directories exist [changed: /etc/php/8.3/fpm/conf.d missing, will create]
        $ sudo mkdir -p /etc/php/8.3/fpm/conf.d
        $ sudo chmod 755 /etc/php/8.3/fpm/conf.d
    ubuntu: Ensure configuration directories exist [changed: /etc/php/8.3/apache2/conf.d missing, will create]
        $ sudo mkdir -p /etc/php/8.3/apache2/conf.d
        $ sudo chmod 755 /etc/php/8.3/apache2/conf.d
    ubuntu: Ensure configuration directories exist [changed: /etc/php/8.3/cli/conf.d missing, will create]
        $ sudo mkdir -p /etc/php/8.3/cli/conf.d
        $ sudo chmod 755 /etc/php/8.3/cli/conf.d
    ubuntu: Place PHP configuration file in place [changed: content /etc/php/8.3/fpm/php.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-8d471761f3da1a8b/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/fpm && cp /tmp/reemote-install-8d471761f3da1a8b/content /etc/php/8.3/fpm/php.ini && chown root:root /etc/php/8.3/fpm/php.ini && chmod 644 /etc/php/8.3/fpm/php.ini && rm -rf /tmp/reemote-install-8d471761f3da1a8b'
    ubuntu: Place PHP configuration file in place [changed: content /etc/php/8.3/apache2/php.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-f4df07cbd5c858a9/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/apache2 && cp /tmp/reemote-install-f4df07cbd5c858a9/content /etc/php/8.3/apache2/php.ini && chown root:root /etc/php/8.3/apache2/php.ini && chmod 644 /etc/php/8.3/apache2/php.ini && rm -rf /tmp/reemote-install-f4df07cbd5c858a9'
    ubuntu: Place PHP configuration file in place [changed: content /etc/php/8.3/cli/php.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-7fcbaa98f7a007e5/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/cli && cp /tmp/reemote-install-7fcbaa98f7a007e5/content /etc/php/8.3/cli/php.ini && chown root:root /etc/php/8.3/cli/php.ini && chmod 644 /etc/php/8.3/cli/php.ini && rm -rf /tmp/reemote-install-7fcbaa98f7a007e5'
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
    ubuntu: Ensure the default pool directory exists [changed: /etc/php/8.3/fpm/pool.d missing, will create]
        $ sudo mkdir -p /etc/php/8.3/fpm/pool.d
        $ sudo chmod 755 /etc/php/8.3/fpm/pool.d
        $ sudo chown root:root /etc/php/8.3/fpm/pool.d
    ubuntu: Create fpm pools         [changed: content /etc/php/8.3/fpm/pool.d/www.conf is absent, will install]
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
        $ sudo systemctl restart php8.3-fpm.service
    ubuntu: Ensure dependencies for building from source are installed (Debian). [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install build-essential
    ubuntu: Ensure Xdebug module path exists. [changed: /usr/lib/php7/modules missing, will create]
        $ sudo mkdir -p /usr/lib/php7/modules
        $ sudo chmod 755 /usr/lib/php7/modules
        $ sudo chown root:root /usr/lib/php7/modules
    ubuntu: Untar Xdebug.            [changed: will extract https://xdebug.org/files/xdebug-3.3.2.tgz into /root]
        $ scp <files unarchive> -> /tmp/reemote-unarchive-8c3a55373f4b95b4-xdebug-3.3.2.tgz
        $ sudo tar -xz -f /tmp/reemote-unarchive-8c3a55373f4b95b4-xdebug-3.3.2.tgz -C /root
        $ sudo rm -rf /tmp/reemote-unarchive-8c3a55373f4b95b4-xdebug-3.3.2.tgz
    barrier: FileExists → False
  wave 12
    barrier: build xdebug → {'exit_status': 0, 'stdout': '', 'stderr': ''}
  wave 13
    ubuntu: Move Xdebug module into place. [changed: will copy /root/xdebug-3.3.2/modules/xdebug.so to /usr/lib/php7/modules/xdebug-3.3.2.so]
        $ sudo cp -r -f /root/xdebug-3.3.2/modules/xdebug.so /usr/lib/php7/modules/xdebug-3.3.2.so
        $ sudo chown root:root /usr/lib/php7/modules/xdebug-3.3.2.so
        $ sudo chmod 644 /usr/lib/php7/modules/xdebug-3.3.2.so
    ubuntu: Copy xdebug ini into main extension config folder. [changed: content /etc/php/8.3/fpm/conf.d/20-xdebug.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-d26f9734c2abb699/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/fpm/conf.d && cp /tmp/reemote-install-d26f9734c2abb699/content /etc/php/8.3/fpm/conf.d/20-xdebug.ini && chown root:root /etc/php/8.3/fpm/conf.d/20-xdebug.ini && chmod 644 /etc/php/8.3/fpm/conf.d/20-xdebug.ini && rm -rf /tmp/reemote-install-d26f9734c2abb699'
    ubuntu: Copy xdebug ini into main extension config folder. [changed: content /etc/php/8.3/apache2/conf.d/20-xdebug.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-ad7d92489b0c88bf/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/apache2/conf.d && cp /tmp/reemote-install-ad7d92489b0c88bf/content /etc/php/8.3/apache2/conf.d/20-xdebug.ini && chown root:root /etc/php/8.3/apache2/conf.d/20-xdebug.ini && chmod 644 /etc/php/8.3/apache2/conf.d/20-xdebug.ini && rm -rf /tmp/reemote-install-ad7d92489b0c88bf'
    ubuntu: Copy xdebug ini into main extension config folder. [changed: content /etc/php/8.3/cli/conf.d/20-xdebug.ini is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-8a008775467a73e2/content
        $ sudo sh -c 'mkdir -p /etc/php/8.3/cli/conf.d && cp /tmp/reemote-install-8a008775467a73e2/content /etc/php/8.3/cli/conf.d/20-xdebug.ini && chown root:root /etc/php/8.3/cli/conf.d/20-xdebug.ini && chmod 644 /etc/php/8.3/cli/conf.d/20-xdebug.ini && rm -rf /tmp/reemote-install-8a008775467a73e2'
    barrier: xdebug install done → 1 host notified
  wave 14
    ubuntu: Restart apache2 (handler) [changed: apache2.service already running; apache2.service will restart]
        $ sudo systemctl restart apache2.service
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
    barrier: FindInFiles → []
  wave 4
    barrier: FindInFiles → []
  wave 5
    barrier: FindInFiles → []
  wave 6
    barrier: FindInFiles → []
  wave 7
    barrier: FindInFiles → []
  wave 8
    barrier: FindInFiles → []
  wave 9
    barrier: FileExists → False
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
    ubuntu: Ensure APCu config file is present [no change: content already up to date (/etc/php/8.3/fpm/conf.d/20-apcu.ini)]
    ubuntu: Ensure APCu config file is present [no change: content already up to date (/etc/php/8.3/apache2/conf.d/20-apcu.ini)]
    ubuntu: Ensure APCu config file is present [no change: content already up to date (/etc/php/8.3/cli/conf.d/20-apcu.ini)]
    ubuntu: Ensure OpCache config file is present [no change: content already up to date (/etc/php/8.3/fpm/conf.d/10-opcache.ini)]
    ubuntu: Ensure OpCache config file is present [no change: content already up to date (/etc/php/8.3/apache2/conf.d/10-opcache.ini)]
    ubuntu: Ensure OpCache config file is present [no change: content already up to date (/etc/php/8.3/cli/conf.d/10-opcache.ini)]
    ubuntu: Ensure the default pool directory exists [no change: directory /etc/php/8.3/fpm/pool.d already exists]
    ubuntu: Create fpm pools         [no change: content already up to date (/etc/php/8.3/fpm/pool.d/www.conf)]
    ubuntu: Ensure php-fpm is started and enabled at boot (if configured) [no change: php8.3-fpm.service already running; php8.3-fpm.service already enabled]
    barrier: configure done → 0 hosts notified
  wave 11
    ubuntu: Ensure dependencies for building from source are installed (Debian). [no change: all packages present]
    ubuntu: Ensure Xdebug module path exists. [no change: directory /usr/lib/php7/modules already exists]
    ubuntu: Untar Xdebug.            [no change: archive already extracted (/root/xdebug-3.3.2 exists)]
    barrier: FileExists → True
  wave 12
    ubuntu: Move Xdebug module into place. [no change: /usr/lib/php7/modules/xdebug-3.3.2.so already exists]
    ubuntu: Copy xdebug ini into main extension config folder. [no change: content already up to date (/etc/php/8.3/fpm/conf.d/20-xdebug.ini)]
    ubuntu: Copy xdebug ini into main extension config folder. [no change: content already up to date (/etc/php/8.3/apache2/conf.d/20-xdebug.ini)]
    ubuntu: Copy xdebug ini into main extension config folder. [no change: content already up to date (/etc/php/8.3/cli/conf.d/20-xdebug.ini)]
    barrier: xdebug install done → 0 hosts notified"""

DRY_PREVIEW_TREE = """(root)
  wave 0
    barrier: LinuxDistribution → unknown until run"""

async def test_tree_snapshot():
    """The composed role as one tree: php's waves, then xdebug's, in a single run.

    This is what T7 exists to prove -- a dependency and its child in one stream, with
    php's three checkpoints and xdebug's one checkpoint visible in the same tree.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)

    assert first.tree() == FIRST_PASS_TREE

    second = await run_role(inventory, hosts)
    assert second.tree() == CONVERGED_TREE
