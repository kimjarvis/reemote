# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``composer`` role (§17.5).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, composer(v), host_factory=...)``. The factory is a plain
(non-``async``) function -- the control-side signature fetch is a per-host
``control`` barrier *inside* the body, gated on that host's ``need_install``, so
it is reached only by a host that actually installs. The test stubs
``files._fetch_url_bytes`` (the seam ``files.fetch_url`` and ``files.download``
share) so the signature and the installer are answered offline and the role's
``sha384:`` checksum pin verifies exactly as it does against getcomposer.org.

The load-bearing assertion is the migration's idempotence claim: run the role,
then run it *again* against the same persistent hosts, and the second pass must
report no change anywhere, fetch nothing and re-run no action -- the composer
binary exists (the ``stat`` gate, so the whole installer half including the
``control`` fetch is skipped), every configured package's ``vendor`` directory
exists (the ``creates=`` gate, so no require action re-runs), and the
home/auth/profile content matches. Under ``run_waves`` the rerun re-derives every
barrier outcome rather than reusing a frozen plan dict, so convergence proves the
barriers themselves are honest.

One simulation limit is bridged in-stream, the sanctioned way (the postgresql
``initdb`` precedent): a raw ``Shell`` action mutates no file store, so the real
installer's ``/tmp/composer.phar`` and each ``composer global require``'s
``vendor/<name>`` directory would be missing when the *next* wave reads them.
``ComposerSimulatedHost`` overrides ``_run_shell`` to plant exactly those side
effects the moment the action succeeds, while the action's *answer* still comes
from ``shell_results`` through ``super()`` -- nothing is faked. The
``copy_path``/``remove_file``/``install_file``/``make_directory`` mutators need
no bridge: they maintain every file store themselves.

A ``dry=True`` pass is one wave deep: the role's first item is the ``FileExists``
stat gate, so the run parks there before the ``control`` barrier and never
fetches, runs an action or applies a request.
"""

import hashlib
import json
from typing import Any

import pytest

from reemote import run_waves
from reemote.modules.files import files as files_module
from reemote.roles import ComposerVars, composer
from reemote.roles.composer import (
    INSTALLER_PATH,
    INSTALLER_URL,
    PHAR_PATH,
    PROFILE_PATH,
    SIGNATURE_URL,
    installer_command,
    render_auth_json,
    render_profile,
    require_command,
    resolve_home,
    resolve_settings,
)
from reemote.rolevars import resolve_vars
from tests.simulated_host import SimulatedHost

DEST = "/usr/local/bin/composer"
DEST_DIR = "/usr/local/bin"
HOME = "/root/.composer"

# The installer is PHP text (the download's text install path); the phar is
# binary (opaque to files.copy, which is content-agnostic).
FAKE_INSTALLER = b"#!/usr/bin/env php\n<?php /* composer-installer */\n"
FAKE_PHAR = b"#!/usr/bin/env php\n<?php /* \xff\xfe\x00composer-phar */"
SIGNATURE = hashlib.sha384(FAKE_INSTALLER).hexdigest()

# The library default for global_packages is the role's own empty list, so a
# test that exercises the require/profile path configures packages explicitly.
PACKAGES = [{"name": "phpunit/phpunit", "release": "@stable"}]

# The exact shells the two action barriers render (the shell_results keys): the
# installer is bare (no sudo, no env), the require carries COMPOSER_HOME + sudo.
INSTALLER_SHELL = "cd /tmp && php composer-installer.php --2"
REQUIRE_SHELL = (
    "sudo env COMPOSER_HOME=/root/.composer /usr/local/bin/composer "
    "global require phpunit/phpunit:@stable --no-progress --no-interaction"
)
OK = {"exit_status": 0, "stdout": "", "stderr": ""}


@pytest.fixture
def canned_fetch(monkeypatch):
    """Replace the control-side fetches with canned bytes, keyed by URL.

    ``files.fetch_url`` (the ``control`` barrier's signature read) and
    ``files.download`` (the installer) share ``_fetch_url_bytes``; patching the
    module attribute redirects both. Returns the list of URLs fetched, so a test
    can assert the signature/installer fetch happened -- and that a converged
    second pass, an already-installed host and a ``dry`` preview fetch nothing.
    """
    fetched: list[str] = []
    bodies = {
        SIGNATURE_URL: SIGNATURE.encode(),
        INSTALLER_URL: FAKE_INSTALLER,
    }

    def fake_fetch(url: str) -> bytes:
        fetched.append(url)
        return bodies[url]

    monkeypatch.setattr(files_module, "_fetch_url_bytes", fake_fetch)
    return fetched


def _plant_file(
    host: SimulatedHost, path: str, data: bytes, mode: int, user: str = "root"
) -> None:
    """Seed a regular file across every store the facts read."""
    host.remote_files[path] = data
    host.existing_files.add(path)
    host.file_stats[path] = {
        "user": user,
        "group": "root",
        "mode": mode,
        "size": len(data),
    }


def _plant_vendor(host: SimulatedHost, home: str, name: str) -> None:
    """Plant the ``vendor/<name>`` directory a real ``global require`` creates.

    The ``creates=`` gate's ``FileExists`` seam reads ``existing_files``; the
    ``directory_stats`` row keeps the store consistent for a later ``directory``
    fact.
    """
    vendor = f"{home}/vendor/{name}"
    host.directory_stats[vendor] = {"mode": 755, "user": "root", "group": "root"}
    host.existing_files.add(vendor)


class ComposerSimulatedHost(SimulatedHost):
    """``SimulatedHost`` with the composer actions' in-stream side effects.

    A raw ``Shell`` action mutates no file store, so the installer's phar and
    each require's vendor directory would be absent when the next wave reads
    them. This plants exactly those, the moment the action succeeds; the
    *answer* still comes from ``shell_results`` through ``super()``, so nothing
    is faked. See the module docstring.
    """

    async def _run_shell(self, shell: str) -> Any:
        result = await super()._run_shell(shell)
        if result.exit_status != 0:
            return result
        if "composer-installer.php" in shell:
            # The real installer writes /tmp/composer.phar; files.copy in the
            # next wave reads it as its source.
            if PHAR_PATH not in self.remote_files:
                _plant_file(self, PHAR_PATH, FAKE_PHAR, 755, user="supervisor")
        elif " global require " in shell:
            # "<sudo env> COMPOSER_HOME=<home> <composer> global require
            #  <name>:<release> ...": plant the vendor dir the require creates.
            home = shell.split("COMPOSER_HOME=", 1)[1].split(" ", 1)[0]
            spec = shell.split(" global require ", 1)[1].split(" ", 1)[0]
            _plant_vendor(self, home, spec.split(":", 1)[0])
        return result


def seed(host: SimulatedHost) -> None:
    """Pre-state: a stock Ubuntu machine, no composer yet.

    ``/usr/local/bin`` and ``/tmp`` exist (files.copy validates the
    destination's parent); root's home answers the ``Home`` fact; the two action
    shells are seeded with success results (an unseeded shell raises, so a test
    that changes a command must seed its own).
    """
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}
    host.directory_stats = {
        DEST_DIR: {"mode": 755, "user": "root", "group": "root"},
        "/tmp": {"mode": 1777, "user": "root", "group": "root"},
    }
    host.existing_files |= set(host.directory_stats)
    host.home_directories = {"root": "/root"}
    host.shell_results = {INSTALLER_SHELL: dict(OK), REQUIRE_SHELL: dict(OK)}
    host.ran_shells = []


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride in the entry's ``roles`` mapping under
    ``"composer"``, bound by ``resolve_vars``; the one raw-extra knob,
    ``php_executable``, stays a top-level entry key read from ``host.extra``
    by ``resolve_settings``.
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
            "composer": {
            },
        },
    }
    entry["roles"]["composer"].update(overrides)
    return [
        entry,
        {
            "host": "web1",
            "family": "RedHat",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "centos",
        },
    ]


def make_hosts(inventory: list[dict[str, Any]]) -> dict[str, ComposerSimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, ComposerSimulatedHost] = {}
    for entry in inventory:
        host = ComposerSimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            seed(host)
        hosts[host.name] = host
    return hosts


async def run_role(
    inventory: list[dict[str, Any]],
    hosts: dict[str, ComposerSimulatedHost],
    *,
    dry: bool = False,
) -> Any:
    """Resolve variables, build the sync factory's deploy, and run one wave pass."""
    v = resolve_vars(ComposerVars, "composer", inventory)

    def factory(entry: dict[str, Any]) -> ComposerSimulatedHost:
        # The *same* host object every pass, so state survives the barriers.
        return hosts[entry["host"]]

    # A plain (non-async) factory: called, not awaited.
    deploy = composer(v)
    report, _hosts = await run_waves(
        inventory, deploy, host_factory=factory, dry=dry
    )
    return report


def changed_entries(report: Any) -> list[Any]:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def entry_names(report: Any) -> set[str]:
    """Every log entry name of one pass."""
    return {entry.name for entry in report.merged().log}


# --- the first pass really installs composer ----------------------------------


async def test_first_pass_installs_composer(canned_fetch):
    """The full stream: installer download + run, phar move, require, profile."""
    inventory = make_inventory(global_packages=PACKAGES)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # Both control-side fetches happened; the checksum pin verified.
    assert SIGNATURE_URL in canned_fetch
    assert INSTALLER_URL in canned_fetch

    # The installer landed at /tmp, the phar moved into place at 0755 and the
    # staged phar was consumed (the mv -> copy + cleanup deviation).
    assert host.remote_files[INSTALLER_PATH] == FAKE_INSTALLER
    assert host.file_stats[INSTALLER_PATH]["mode"] == 755
    assert host.remote_files[DEST] == FAKE_PHAR
    assert host.file_stats[DEST]["mode"] == 755
    assert PHAR_PATH not in host.remote_files
    assert PHAR_PATH not in host.existing_files

    # The home directory exists at 0755 root:root; the profile entry landed.
    assert host.directory_stats[HOME]["mode"] == 755
    assert host.remote_files[PROFILE_PATH] == (
        b"export PATH=$PATH:/root/.composer/vendor/bin\n"
    )

    # Both action barriers fired exactly once, with the exact rendered shells.
    assert host.ran_shells == [INSTALLER_SHELL, REQUIRE_SHELL]

    # No token configured: the auth.json task was not even requested.
    assert "Add GitHub OAuth token for Composer." not in entry_names(report)
    assert changed_entries(report)


async def test_non_ubuntu_host_is_never_touched(canned_fetch):
    inventory = make_inventory(global_packages=PACKAGES)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    skipped = hosts["web1"]
    assert skipped.remote_files == {}
    assert skipped.directory_stats == {}
    assert skipped.ran_shells == []
    # The family guard returns before the first yield, so the off-target host
    # emits no log entry at all.
    assert not any(entry.host == "web1" for entry in report.merged().log)


# --- the second pass is a no-op -----------------------------------------------


async def test_second_pass_changes_nothing(canned_fetch):
    inventory = make_inventory(global_packages=PACKAGES)
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"
    fetches_after_first = len(canned_fetch)
    shells_after_first = list(hosts["ubuntu"].ran_shells)

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    # No signature fetch, no installer download, no action re-run: the barriers
    # re-derived need_install=False and an empty missing-package list.
    assert len(canned_fetch) == fetches_after_first
    assert hosts["ubuntu"].ran_shells == shells_after_first


async def test_dry_preview_mutates_nothing(canned_fetch):
    """``dry=True`` is one wave deep, so it parks at the stat gate.

    Unlike the old two-phase script -- whose prepare phase fetched the signature
    control-side -- the wave driver's dry pass never reaches the ``control``
    barrier, so it fetches nothing, runs no action and mutates no store.
    """
    inventory = make_inventory(global_packages=PACKAGES)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    before_dirs = dict(host.directory_stats)

    await run_role(inventory, hosts, dry=True)
    assert canned_fetch == []
    assert host.remote_files == {}
    assert host.file_stats == {}
    assert host.directory_stats == before_dirs
    assert host.ran_shells == []


# --- the optional auth.json branch ---------------------------------------------


async def test_auth_token_installs_auth_json(canned_fetch):
    inventory = make_inventory(github_oauth_token="ghp_seedtoken")
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    expected = json.dumps({"github-oauth": {"github.com": "ghp_seedtoken"}}, indent=4)
    assert host.remote_files[f"{HOME}/auth.json"] == (expected + "\n").encode()
    assert host.file_stats[f"{HOME}/auth.json"]["mode"] == 644
    auth = next(
        entry
        for entry in report.merged().log
        if entry.name == "Add GitHub OAuth token for Composer."
    )
    assert auth.changed

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the package loop and profile gates -----------------------------------------


async def test_no_packages_skips_require_and_profile(canned_fetch):
    """The library default is the role's own empty package list."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    assert host.ran_shells == [INSTALLER_SHELL]
    assert PROFILE_PATH not in host.remote_files
    assert host.remote_files[DEST] == FAKE_PHAR
    assert changed_entries(report)

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_add_to_path_false_skips_the_profile(canned_fetch):
    inventory = make_inventory(
        global_packages=PACKAGES, add_to_path=False
    )
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    assert PROFILE_PATH not in hosts["ubuntu"].remote_files
    # The require action still runs; only the PATH entry is gated off.
    assert hosts["ubuntu"].ran_shells == [INSTALLER_SHELL, REQUIRE_SHELL]

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- version pinning and custom paths -------------------------------------------


async def test_version_pin_requires_an_empty_branch(canned_fetch):
    inventory = make_inventory(
        global_packages=PACKAGES,
        version="2.8.12",
        version_branch="",
    )
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    pinned_shell = "cd /tmp && php composer-installer.php --version=2.8.12"
    host.shell_results[pinned_shell] = dict(OK)

    await run_role(inventory, hosts)
    assert host.ran_shells == [pinned_shell, REQUIRE_SHELL]

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_custom_paths(canned_fetch):
    inventory = make_inventory(
        global_packages=PACKAGES,
        path="/opt/bin/composer",
        home_path="/opt/composer-home",
    )
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    # files.copy validates the destination's parent.
    host.directory_stats["/opt/bin"] = {"mode": 755, "user": "root", "group": "root"}
    host.existing_files.add("/opt/bin")
    custom_require = (
        "sudo env COMPOSER_HOME=/opt/composer-home /opt/bin/composer "
        "global require phpunit/phpunit:@stable --no-progress --no-interaction"
    )
    host.shell_results[custom_require] = dict(OK)

    await run_role(inventory, hosts)
    assert host.remote_files["/opt/bin/composer"] == FAKE_PHAR
    assert DEST not in host.remote_files
    assert host.directory_stats["/opt/composer-home"]["mode"] == 755
    assert host.remote_files[PROFILE_PATH] == (
        b"export PATH=$PATH:/opt/composer-home/vendor/bin\n"
    )
    assert host.ran_shells == [INSTALLER_SHELL, custom_require]

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- an already-installed composer ----------------------------------------------


async def test_already_installed_skips_the_installer(canned_fetch):
    """A converged binary skips the whole installer half -- the fetch included."""
    inventory = make_inventory(global_packages=PACKAGES)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    _plant_file(host, DEST, FAKE_PHAR, 755)

    await run_role(inventory, hosts)
    # No signature fetch, no installer download, no installer action: the
    # per-host control gate never opens.
    assert canned_fetch == []
    assert host.ran_shells == [REQUIRE_SHELL]
    assert INSTALLER_PATH not in host.remote_files
    # The home/profile half still runs.
    assert host.directory_stats[HOME]["mode"] == 755
    assert PROFILE_PATH in host.remote_files

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the pure helpers, unit-tested directly --------------------------------------


def _host(**extra: Any) -> SimulatedHost:
    """A host from one entry; ``extra`` rides the raw top-level extra channel."""
    entry = make_inventory()[0]
    entry.update(extra)
    return SimulatedHost.from_inventory(entry)


def test_resolve_settings_defaults():
    settings = resolve_settings(ComposerVars(), _host())
    assert settings.composer_path == DEST
    assert settings.php == "php"
    assert settings.installer_arg == "--2"
    assert settings.packages == ()
    assert settings.auth_content is None


def test_php_executable_is_read_raw_from_extra():
    # The role's one unprefixed variable is not a ComposerVars field.
    settings = resolve_settings(ComposerVars(), _host(php_executable="php8.3"))
    assert settings.php == "php8.3"


def test_installer_arg_branch_wins_over_version():
    settings = resolve_settings(ComposerVars(version="2.8.12"), _host())
    # The role's {% if branch %}...{% elif version %} precedence.
    assert settings.installer_arg == "--2"


def test_installer_arg_version_when_branch_empty():
    settings = resolve_settings(
        ComposerVars(version="2.8.12", version_branch=""), _host()
    )
    assert settings.installer_arg == "--version=2.8.12"


def test_installer_and_require_command_text():
    settings = resolve_settings(ComposerVars(), _host())
    assert installer_command(settings) == INSTALLER_SHELL
    assert (
        require_command(DEST, "phpunit/phpunit", "@stable")
        == "/usr/local/bin/composer global require phpunit/phpunit:@stable "
        "--no-progress --no-interaction"
    )


def test_global_packages_accepts_json_dicts():
    hv = ComposerVars(
        global_packages=[
            {"name": "phpunit/phpunit", "release": "4.7.x"},
            {"name": "squizlabs/php_codesniffer"},
        ]
    )
    assert hv.global_packages == (
        ("phpunit/phpunit", "4.7.x"),
        ("squizlabs/php_codesniffer", "@stable"),
    )


def test_global_packages_accepts_pairs():
    hv = ComposerVars(global_packages=[("phpunit/phpunit", "@stable")])
    assert hv.global_packages == (("phpunit/phpunit", "@stable"),)


def test_resolve_home_forms():
    assert resolve_home("/opt/home", None) == "/opt/home"
    assert resolve_home("~", "/root") == "/root"
    assert resolve_home("~/.composer", "/root") == "/root/.composer"
    with pytest.raises(ValueError, match="composer_home_path"):
        resolve_home("~/.composer", None)


def test_render_auth_json_and_profile():
    assert render_auth_json("tok") == (
        json.dumps({"github-oauth": {"github.com": "tok"}}, indent=4) + "\n"
    )
    assert render_profile("/root/.composer") == (
        "export PATH=$PATH:/root/.composer/vendor/bin\n"
    )


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"keep_updated": True}, "scoped out"),
        ({"keep_updated": True, "version": "2.8.12"}, "not both"),
        ({"add_project_to_path": True}, "scoped out"),
        ({"path": "bin/composer"}, "composer_path"),
        ({"path": ""}, "composer_path"),
        ({"home_path": "relative/dir"}, "composer_home_path"),
        ({"home_owner": ""}, "composer_home_owner"),
        ({"home_group": ""}, "composer_home_owner"),
        ({"version": 42}, "version"),
        ({"add_to_path": "maybe"}, "add_to_path"),
        ({"global_packages": "phpunit"}, "composer_global_packages"),
        ({"global_packages": [{"release": "@stable"}]}, "name"),
        ({"global_packages": [{"name": "x", "release": ""}]}, "release"),
    ],
)
def test_vars_reject_invalid_values(kwargs, match):
    # pydantic's ValidationError is a ValueError; so is every validator raise.
    with pytest.raises(ValueError, match=match):
        ComposerVars(**kwargs)


@pytest.mark.parametrize("value", ["yes", "no", "true", "false", "on", "off", 1, 0])
def test_add_to_path_accepts_yaml_booleans(value):
    assert isinstance(ComposerVars(add_to_path=value).add_to_path, bool)


def test_empty_php_executable_raises():
    with pytest.raises(ValueError, match="php_executable"):
        resolve_settings(ComposerVars(), _host(php_executable=""))
