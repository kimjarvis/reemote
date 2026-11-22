# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end test for the Composer Ansible-role migration example.

``examples/roles/ansible-role-composer/ansible-role-composer.py`` connects for
real when run as a script, so the suite never executes its ``main()``. What it
*can* do offline is replay the same orchestration against ``SimulatedHost``:
the example is imported as a module, its three factories are fed the frozen
plans gathered from simulated state, and every deploy goes through the real
driver (prepare + apply, plus a ``dry=True`` preview), with the two Shell
action barriers between them answered by the ``shell_results`` seam (#16).

The load-bearing assertion is the migration's idempotence claim: run the
gather -> plan -> deploy/action sequence, then run it *again* against the same
persistent hosts, and the second pass must report ``changed=False`` everywhere
and fire no action at all -- the composer binary exists (the stat gate), the
vendor directories exist (the creates= gate), the home/auth/profile content
matches.

Between-pass bridges (simulation limits, not library bugs): the shell seam
models no filesystem effects, so after the installer action the test plants
``/tmp/composer.phar`` (content + existence + stat row) by hand -- what the
real installer writes -- and after each global require it plants the
package's ``vendor/<name>`` directory into ``directory_stats`` *and*
``existing_files`` (the ``FileExists`` seam ``test -e`` reads). The
``install_file``/``copy_path``/``remove_file`` mutators need no bridge: they
maintain every file store themselves.

The control-side fetches are answered by monkeypatching
``files._fetch_url_bytes``: the signature URL returns the sha384 of the canned
installer text, so the role's checksum pin verifies offline exactly as it
does against getcomposer.org.
"""

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from reemote import FileExists, Shell, gather_action, gather_fact, run, server
from reemote.modules.files import files as files_module
from tests.simulated_host import SimulatedHost

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = (
    REPO_ROOT
    / "examples"
    / "roles"
    / "ansible-role-composer"
    / "ansible-role-composer.py"
)


def load_example() -> Any:
    """Import the migration example by path (its name is not a valid identifier)."""
    spec = importlib.util.spec_from_file_location(
        "ansible_role_composer_example", EXAMPLE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


composer = load_example()

DEST = "/usr/local/bin/composer"
DEST_DIR = "/usr/local/bin"
INSTALLER_PATH = "/tmp/composer-installer.php"
PHAR = "/tmp/composer.phar"
HOME = "/root/.composer"
PROFILE = "/etc/profile.d/composer.sh"
VENDOR = f"{HOME}/vendor/phpunit/phpunit"

# The installer is PHP text (the download's text install path); the phar is
# binary (opaque to files.copy, which is content-agnostic).
FAKE_INSTALLER = b"#!/usr/bin/env php\n<?php /* composer-installer */\n"
FAKE_PHAR = b"#!/usr/bin/env php\n<?php /* \xff\xfe\x00composer-phar */"
SIGNATURE = hashlib.sha384(FAKE_INSTALLER).hexdigest()

# The exact shells the two action barriers render (the shell_results keys).
INSTALLER_SHELL = "cd /tmp && php composer-installer.php --2"
REQUIRE_SHELL = (
    "sudo env COMPOSER_HOME=/root/.composer /usr/local/bin/composer "
    "global require phpunit/phpunit:@stable --no-progress --no-interaction"
)
OK = {"exit_status": 0, "stdout": "", "stderr": ""}


@pytest.fixture
def canned_fetch(monkeypatch):
    """Replace the control-side fetches with canned bytes, keyed by URL.

    ``files.fetch_url`` and ``files.download`` share ``_fetch_url_bytes``;
    patching the module attribute redirects both. Returns the list of URLs
    fetched, so a test can assert the signature/installer fetch happened --
    and that a converged second pass fetches nothing.
    """
    fetched: list[str] = []
    bodies = {
        composer.SIGNATURE_URL: SIGNATURE.encode(),
        composer.INSTALLER_URL: FAKE_INSTALLER,
    }

    def fake_fetch(url: str) -> bytes:
        fetched.append(url)
        return bodies[url]

    monkeypatch.setattr(files_module, "_fetch_url_bytes", fake_fetch)
    return fetched


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride as extra keys -- reemote's ``host_vars`` -- so
    ``role_settings`` reads them straight off ``host.extra``. With no overrides
    the demo defaults apply (D3: phpunit/phpunit @stable globally required).
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


def seed(host: SimulatedHost) -> None:
    """Pre-state: a stock Ubuntu machine, no composer yet.

    ``/usr/local/bin`` and ``/tmp`` exist (files.copy validates the
    destination's parent in both phases); root's home answers the Home fact;
    the two action shells are seeded with success results.
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


def make_hosts(inventory: list[dict[str, Any]]) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if composer.is_ubuntu(host):
            seed(host)
        hosts[host.name] = host
    return hosts


async def migrate(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SimulatedHost],
    *,
    dry: bool = True,
    apply: bool = True,
) -> dict[str, Any]:
    """Replay the example's whole orchestration once, against persistent hosts.

    Mirrors ``main()`` barrier for barrier -- variable resolution, the three
    fact gathers, the gated signature fetch, deploy 1, the installer action,
    deploy 2, the global-require actions, deploy 3 -- using the example's own
    pure helpers so the replay cannot drift from the program. ``apply=False``
    stops after deploy 1's dry preview (reports empty).
    """
    targets = [host for host in hosts.values() if composer.is_ubuntu(host)]
    resolved = {
        host.name: composer.resolved_vars(composer.role_settings(host))
        for host in targets
    }

    gathered: dict[str, Any] = {}
    try:
        for host in targets:
            rv = resolved[host.name]
            exists = (await gather_fact([host], FileExists, rv["composer_path"]))[
                host.name
            ]
            home_base = (
                await gather_fact([host], server.Home, user=rv["home_owner"])
            )[host.name]
            home = composer.resolve_home(rv["home_path"], home_base)
            missing: list[tuple[str, str]] = []
            for name, release in rv["packages"]:
                vendor = (
                    await gather_fact([host], FileExists, f"{home}/vendor/{name}")
                )[host.name]
                if not vendor:
                    missing.append((name, release))
            gathered[host.name] = (not exists, home, tuple(missing))
    finally:
        for host in targets:
            await host.aclose()

    signature = ""
    if any(need for need, _home, _require in gathered.values()):
        signature = (await files_module.fetch_url(composer.SIGNATURE_URL)).strip()
    plans = {
        host.name: composer.freeze_plan(
            resolved[host.name],
            need_install=gathered[host.name][0],
            home=gathered[host.name][1],
            signature=signature,
            require=gathered[host.name][2],
        )
        for host in targets
    }

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        # The *same* host object every run, so state survives the barriers.
        return hosts[entry["host"]]

    async def run_deploy(deploy: Any) -> Any:
        if dry:
            await run(inventory, deploy, host_factory=factory, dry=True)
        _plans, report, _hosts = await run(inventory, deploy, host_factory=factory)
        assert report is not None
        return report

    reports: list[Any] = []
    if any(plan["need_install"] for plan in plans.values()):
        deploy1 = composer.build_download(plans)
        if dry:
            await run(inventory, deploy1, host_factory=factory, dry=True)
        if not apply:
            return {"reports": [], "plans": plans}
        _plans, report, _hosts = await run(inventory, deploy1, host_factory=factory)
        assert report is not None
        reports.append(report)
        for host in targets:
            plan = plans[host.name]
            if not plan["need_install"]:
                continue
            command = composer.installer_command(plan)
            result = (await gather_action([host], Shell, command=command))[host.name]
            assert result["exit_status"] == 0
            # Bridge: the shell seam models no filesystem effects. Plant the
            # phar the real installer writes, so deploy 2's files.copy sees
            # its source in both phases.
            _plant_file(host, PHAR, FAKE_PHAR, 755, user="supervisor")

    reports.append(await run_deploy(composer.build_configure(plans)))

    for host in targets:
        plan = plans[host.name]
        for name, release in plan["require"]:
            command = composer.require_command(plan["composer_path"], name, release)
            result = (
                await gather_action(
                    [host],
                    Shell,
                    command=command,
                    env={"COMPOSER_HOME": plan["home"]},
                    sudo=True,
                )
            )[host.name]
            assert result["exit_status"] == 0
            # Bridge: plant the vendor directory the real require creates --
            # the creates= gate's FileExists seam reads existing_files.
            vendor = f"{plan['home']}/vendor/{name}"
            host.directory_stats[vendor] = {"mode": 755, "user": "root", "group": "root"}
            host.existing_files.add(vendor)

    if any(plan["profile_content"] is not None for plan in plans.values()):
        reports.append(await run_deploy(composer.build_profile(plans)))

    return {"reports": reports, "plans": plans}


def changed_entries(result: dict[str, Any]) -> list[Any]:
    """Every changed log entry across the pass's deploys."""
    return [
        entry
        for report in result["reports"]
        for entry in report.log
        if entry.changed
    ]


# --- the first pass really installs composer ----------------------------------


async def test_first_pass_installs_composer(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    result = await migrate(inventory, hosts)
    host = hosts["ubuntu"]

    # The plan froze the stat gate and the creates= gate.
    assert result["plans"]["ubuntu"]["need_install"] is True
    assert result["plans"]["ubuntu"]["require"] == (("phpunit/phpunit", "@stable"),)
    assert result["plans"]["ubuntu"]["signature"] == SIGNATURE

    # Both control-side fetches happened; the checksum pin verified.
    assert composer.SIGNATURE_URL in canned_fetch
    assert composer.INSTALLER_URL in canned_fetch

    # The installer landed at /tmp, the phar moved into place at 0755 and
    # the staged phar was consumed (D5's paired cleanup).
    assert host.remote_files[INSTALLER_PATH] == FAKE_INSTALLER
    assert host.file_stats[INSTALLER_PATH]["mode"] == 755
    assert host.remote_files[DEST] == FAKE_PHAR
    assert host.file_stats[DEST]["mode"] == 755
    assert PHAR not in host.remote_files
    assert PHAR not in host.existing_files

    # The home directory exists at 0755 root:root; the profile entry landed.
    assert host.directory_stats[HOME]["mode"] == 755
    assert host.remote_files[PROFILE] == b"export PATH=$PATH:/root/.composer/vendor/bin\n"

    # Both action barriers fired exactly once, with the exact rendered shells.
    assert host.ran_shells == [INSTALLER_SHELL, REQUIRE_SHELL]

    # No token configured: the auth.json task was not even requested.
    names = {entry.name for report in result["reports"] for entry in report.log}
    assert "Add GitHub OAuth token for Composer." not in names
    assert changed_entries(result)


async def test_non_ubuntu_host_is_never_touched(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    result = await migrate(inventory, hosts)

    skipped = hosts["web1"]
    assert skipped.remote_files == {}
    assert skipped.directory_stats == {}
    assert skipped.ran_shells == []
    for report in result["reports"]:
        for entry in report.log:
            if entry.host == "web1":
                assert entry.skipped
                assert not entry.changed


# --- the second pass is a no-op -----------------------------------------------


async def test_second_pass_changes_nothing(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await migrate(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"
    fetches_after_first = len(canned_fetch)
    shells_after_first = list(hosts["ubuntu"].ran_shells)

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []
    assert second["plans"]["ubuntu"]["need_install"] is False
    assert second["plans"]["ubuntu"]["require"] == ()
    # No signature fetch, no installer download, no action re-run.
    assert len(canned_fetch) == fetches_after_first
    assert hosts["ubuntu"].ran_shells == shells_after_first


async def test_dry_preview_mutates_nothing(canned_fetch):
    """``dry=True`` stops after prepare, so it must not touch the target.

    The prepare phase does fetch the signature and the installer control-side
    (the body must be read to plan the sha384-verified install), but no host
    store may change.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    before_dirs = dict(host.directory_stats)

    result = await migrate(inventory, hosts, apply=False)
    assert result["reports"] == []
    assert result["plans"]["ubuntu"]["need_install"] is True
    assert canned_fetch  # the control-side fetches happened during prepare
    assert host.remote_files == {}
    assert host.existing_files == set(before_dirs)
    assert host.file_stats == {}
    assert host.directory_stats == before_dirs
    assert host.ran_shells == []


# --- the optional auth.json branch ---------------------------------------------


async def test_auth_token_installs_auth_json(canned_fetch):
    inventory = make_inventory(composer_github_oauth_token="ghp_seedtoken")
    hosts = make_hosts(inventory)
    result = await migrate(inventory, hosts)
    host = hosts["ubuntu"]

    expected = json.dumps({"github-oauth": {"github.com": "ghp_seedtoken"}}, indent=4)
    assert host.remote_files[f"{HOME}/auth.json"] == (expected + "\n").encode()
    assert host.file_stats[f"{HOME}/auth.json"]["mode"] == 644
    auth = next(
        entry
        for report in result["reports"]
        for entry in report.log
        if entry.name == "Add GitHub OAuth token for Composer."
    )
    assert auth.changed

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


# --- the package loop and profile gates -----------------------------------------


async def test_no_packages_skips_require_and_profile(canned_fetch):
    inventory = make_inventory(composer_global_packages=[])
    hosts = make_hosts(inventory)
    result = await migrate(inventory, hosts)
    host = hosts["ubuntu"]

    assert result["plans"]["ubuntu"]["require"] == ()
    assert host.ran_shells == [INSTALLER_SHELL]
    assert PROFILE not in host.remote_files
    assert host.remote_files[DEST] == FAKE_PHAR

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


async def test_add_to_path_false_skips_the_profile(canned_fetch):
    inventory = make_inventory(composer_add_to_path=False)
    hosts = make_hosts(inventory)
    result = await migrate(inventory, hosts)

    assert result["plans"]["ubuntu"]["profile_content"] is None
    assert PROFILE not in hosts["ubuntu"].remote_files
    assert hosts["ubuntu"].ran_shells == [INSTALLER_SHELL, REQUIRE_SHELL]

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


# --- version pinning and custom paths -------------------------------------------


async def test_version_pin_requires_an_empty_branch(canned_fetch):
    inventory = make_inventory(composer_version="2.8.12", composer_version_branch="")
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    pinned_shell = "cd /tmp && php composer-installer.php --version=2.8.12"
    host.shell_results[pinned_shell] = dict(OK)

    result = await migrate(inventory, hosts)
    assert result["plans"]["ubuntu"]["installer_arg"] == "--version=2.8.12"
    assert host.ran_shells == [pinned_shell, REQUIRE_SHELL]

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


async def test_custom_paths(canned_fetch):
    inventory = make_inventory(
        composer_path="/opt/bin/composer", composer_home_path="/opt/composer-home"
    )
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    # files.copy validates the destination's parent in both phases.
    host.directory_stats["/opt/bin"] = {"mode": 755, "user": "root", "group": "root"}
    host.existing_files.add("/opt/bin")
    custom_require = (
        "sudo env COMPOSER_HOME=/opt/composer-home /opt/bin/composer "
        "global require phpunit/phpunit:@stable --no-progress --no-interaction"
    )
    host.shell_results[custom_require] = dict(OK)

    result = await migrate(inventory, hosts)
    plan = result["plans"]["ubuntu"]
    assert plan["home"] == "/opt/composer-home"
    assert host.remote_files["/opt/bin/composer"] == FAKE_PHAR
    assert DEST not in host.remote_files
    assert host.directory_stats["/opt/composer-home"]["mode"] == 755
    assert host.remote_files[PROFILE] == (
        b"export PATH=$PATH:/opt/composer-home/vendor/bin\n"
    )
    assert host.ran_shells == [INSTALLER_SHELL, custom_require]

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


# --- an already-installed composer ----------------------------------------------


async def test_already_installed_skips_the_installer(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    _plant_file(host, DEST, FAKE_PHAR, 755)

    result = await migrate(inventory, hosts)
    assert result["plans"]["ubuntu"]["need_install"] is False
    # No signature fetch, no installer download, no installer action.
    assert canned_fetch == []
    assert host.ran_shells == [REQUIRE_SHELL]
    assert INSTALLER_PATH not in host.remote_files
    # The home/profile half still converges.
    assert host.directory_stats[HOME]["mode"] == 755
    assert PROFILE in host.remote_files

    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []


# --- the pure helpers, unit-tested directly --------------------------------------


def test_resolved_vars_defaults():
    settings = composer.role_settings(
        SimulatedHost.from_inventory(make_inventory()[0])
    )
    rv = composer.resolved_vars(settings)
    assert rv["composer_path"] == DEST
    assert rv["php"] == "php"
    assert rv["installer_arg"] == "--2"
    assert rv["packages"] == (("phpunit/phpunit", "@stable"),)
    assert rv["auth_content"] is None


def test_installer_arg_branch_wins_over_version():
    entry = make_inventory(composer_version="2.8.12")[0]
    rv = composer.resolved_vars(
        composer.role_settings(SimulatedHost.from_inventory(entry))
    )
    # The role's {% if branch %}...{% elif version %} precedence.
    assert rv["installer_arg"] == "--2"


def test_global_packages_accepts_json_dicts():
    entry = make_inventory(
        composer_global_packages=[
            {"name": "phpunit/phpunit", "release": "4.7.x"},
            {"name": "squizlabs/php_codesniffer"},
        ]
    )[0]
    settings = composer.role_settings(SimulatedHost.from_inventory(entry))
    assert settings["composer_global_packages"] == (
        ("phpunit/phpunit", "4.7.x"),
        ("squizlabs/php_codesniffer", "@stable"),
    )


def test_resolve_home_forms():
    assert composer.resolve_home("/opt/home", None) == "/opt/home"
    assert composer.resolve_home("~", "/root") == "/root"
    assert composer.resolve_home("~/.composer", "/root") == "/root/.composer"
    with pytest.raises(ValueError, match="composer_home_path"):
        composer.resolve_home("~/.composer", None)


@pytest.mark.parametrize(
    ("overrides", "error", "match"),
    [
        ({"composer_keep_updated": True}, ValueError, "scoped out"),
        (
            {"composer_keep_updated": True, "composer_version": "2.8.12"},
            ValueError,
            "not both",
        ),
        ({"composer_add_project_to_path": True}, ValueError, "scoped out"),
        ({"composer_path": "bin/composer"}, ValueError, "composer_path"),
        ({"composer_path": ""}, ValueError, "composer_path"),
        ({"composer_home_path": "relative/dir"}, ValueError, "composer_home_path"),
        ({"composer_home_owner": ""}, ValueError, "composer_home_owner"),
        ({"php_executable": ""}, ValueError, "php_executable"),
        ({"composer_version": 42}, TypeError, "composer_version"),
        ({"composer_add_to_path": "maybe"}, ValueError, "composer_add_to_path"),
        ({"composer_global_packages": "phpunit"}, TypeError, "composer_global_packages"),
        ({"composer_global_packages": [{"release": "@stable"}]}, ValueError, "name"),
        ({"composer_global_packages": [{"name": "x", "release": ""}]}, ValueError, "release"),
    ],
)
def test_role_settings_rejects_invalid_values(overrides, error, match):
    host = SimulatedHost.from_inventory(make_inventory(**overrides)[0])
    with pytest.raises(error, match=match):
        composer.role_settings(host)


@pytest.mark.parametrize("value", ["yes", "no", "true", "false", "on", "off", 1, 0])
def test_add_to_path_accepts_yaml_booleans(value):
    entry = make_inventory(composer_add_to_path=value)[0]
    settings = composer.role_settings(SimulatedHost.from_inventory(entry))
    assert isinstance(settings["composer_add_to_path"], bool)