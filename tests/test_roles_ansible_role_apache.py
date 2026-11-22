# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end test for the Apache Ansible-role migration example.

``examples/roles/ansible-role-apache/ansible-role-apache.py`` connects for
real when run as a script, so the suite never executes its ``main()``. What
it *can* do offline is replay the same orchestration against
``SimulatedHost``: the example is imported as a module, its factories are
fed snapshots gathered from simulated state, and every deploy goes through
the real driver (prepare + apply, plus a ``dry=True`` preview).

The load-bearing assertion is the migration's idempotence claim: run the
whole install -> probe -> configure -> handler sequence, then run it *again*
against the same persistent hosts, and the second pass must report
``changed=False`` everywhere and queue no restart handler. Unlike the
DataDog migration, no between-pass bridge is needed here: every subsystem
the deploys touch (apt's update stamp, the file/link stores, systemd's
service state) is maintained by the simulation's own mutators.

The version probe is answered offline by the first-class action seam (#16):
``seed()`` plants the canned ``apache2 -v`` record in ``host.shell_results``
and ``gather_action(targets, Shell, ...)`` reads it through
``SimulatedHost._run_shell`` -- every shell the seam ran is recorded in
``ran_shells``, which the exactly-once assertions below read.

Another seeding choice, not a bridge: the simulated apt install does not
materialize a package's own files, so ``ports.conf``, the default-vhost
symlink and the ``/etc/apache2`` directory tree are seeded as the package's
post-install state -- the configure deploy runs after the install apply,
when a real machine has them.
"""

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from reemote import FileExists, Shell, changed_hosts, gather_action, gather_fact, run
from tests.simulated_host import SimulatedHost

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = (
    REPO_ROOT / "examples" / "roles" / "ansible-role-apache" / "ansible-role-apache.py"
)


def load_example() -> Any:
    """Import the migration example by path (its name is not a valid identifier)."""
    spec = importlib.util.spec_from_file_location("ansible_role_apache_example", EXAMPLE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


apache = load_example()

PROBE = "/usr/sbin/apache2 -v"
VERSION_STDOUT = (
    "Server version: Apache/2.4.58 (Ubuntu)\n"
    "Server built:   2026-01-01T00:00:00\n"
)

PORTS_CONF = "/etc/apache2/ports.conf"
SITES_AVAILABLE = "/etc/apache2/sites-available"
SITES_ENABLED = "/etc/apache2/sites-enabled"
MODS_ENABLED = "/etc/apache2/mods-enabled"
MODS_AVAILABLE = "/etc/apache2/mods-available"
CONF_ENABLED = "/etc/apache2/conf-enabled"
CONF_AVAILABLE = "/etc/apache2/conf-available"
DEFAULT_VHOST_LINK = f"{SITES_ENABLED}/000-default.conf"
VHOSTS_FILE = f"{SITES_AVAILABLE}/vhosts.conf"
VHOSTS_LINK = f"{SITES_ENABLED}/vhosts.conf"
CERT_FILE = "/etc/ssl/certs/secure.pem"
KEY_FILE = "/etc/ssl/private/secure.key"


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride as extra keys -- reemote's ``host_vars`` -- so
    ``role_settings`` reads them straight off ``host.extra``. The overrides
    exercise the non-default branches: a custom port, the default-vhost
    removal, a disabled mod, an enabled conf, and a vhost with the optional
    alias/extra-parameters fields.
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
        "apache_listen_port": 8080,
        "apache_remove_default_vhost": True,
        "apache_mods_disabled": ["autoindex"],
        "apache_conf_enabled": ["security"],
        "apache_vhosts": [
            {
                "servername": "example.com",
                "serveralias": "www.example.com",
                "documentroot": "/var/www/example",
                "extra_parameters": (
                    "ErrorLog /var/log/apache2/example-error.log\n"
                    "CustomLog /var/log/apache2/example-access.log combined"
                ),
            }
        ],
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
    """Pre-state: a stock Ubuntu machine with Apache not yet installed.

    The package's own files (``ports.conf``, the default-vhost symlink, the
    ``/etc/apache2`` tree) are seeded as its post-install state: the
    simulation's apt install records package names only, and the configure
    deploy runs after the install apply, when a real machine has them.
    """
    host.apt_packages = {}
    host.apt_update_stamp = None
    host.remote_files = {PORTS_CONF: b"Listen 80\n"}
    host.existing_files = {PORTS_CONF}
    host.file_stats = {
        PORTS_CONF: {"user": "root", "group": "root", "mode": 644, "size": len(b"Listen 80\n")}
    }
    host.links = {
        DEFAULT_VHOST_LINK: f"{SITES_AVAILABLE}/000-default.conf",
        f"{MODS_ENABLED}/autoindex.load": f"{MODS_AVAILABLE}/autoindex.load",
    }
    host.link_stats = {
        path: {"mode": 777, "user": "root", "group": "root", "link_target": target}
        for path, target in host.links.items()
    }
    host.directory_stats = {
        path: {"mode": 755, "user": "root", "group": "root"}
        for path in (
            "/etc/apache2",
            MODS_ENABLED,
            MODS_AVAILABLE,
            CONF_ENABLED,
            CONF_AVAILABLE,
            SITES_ENABLED,
            SITES_AVAILABLE,
        )
    }
    host.services = {}
    host.enabled_services = set()
    # The version probe rides the first-class action seam (#16): the exact
    # shell ``gather_action`` renders is the key, and the canned record is
    # the ``apache2 -v`` answer the version-table selection consumes.
    host.shell_results[PROBE] = {
        "exit_status": 0,
        "stdout": VERSION_STDOUT,
        "stderr": "",
    }


def make_hosts(inventory: list[dict[str, Any]]) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if apache.is_ubuntu(host):
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

    The sequence mirrors ``main()`` barrier for barrier -- the probe and the
    certificate reads sit in the same gaps between the same runs -- but reads
    the simulated hosts instead of opening SSH connections.
    """
    targets = [host for host in hosts.values() if apache.is_ubuntu(host)]
    settings = {host.name: apache.role_settings(host) for host in targets}
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

    # Deploy 1: the apt cache refresh and the packages.
    await execute(apache.deploy_install)

    # The version probe, after the install apply (the seeded seam answers it).
    results = await gather_action(targets, Shell, command=PROBE)
    versions = {}
    for host in targets:
        result = results[host.name]
        assert result["exit_status"] == 0
        versions[host.name] = apache.parse_apache_version(result["stdout"])

    # The certificate existence reads the vhosts template consumes.
    ssl_exists: dict[str, tuple[bool, ...]] = {}
    try:
        for host in targets:
            paths = [
                vhost["certificate_file"] for vhost in settings[host.name]["vhosts_ssl"]
            ]
            exists = []
            for path in paths:
                gathered = await gather_fact([host], FileExists, path)
                exists.append(bool(gathered[host.name]))
            ssl_exists[host.name] = tuple(exists)
    finally:
        for host in targets:
            await host.aclose()

    # Freeze the configure plan and run deploy 2.
    plans = {
        host.name: apache.configure_plan(
            settings[host.name], versions[host.name], ssl_exists[host.name]
        )
        for host in targets
    }
    configure_report = await execute(apache.build_configure(plans))

    # The handler: changed_hosts over the notifying names, then deploy 3.
    names = tuple(
        sorted({name for plan in plans.values() for name in apache.notifying_names(plan)})
    )
    notified = changed_hosts(configure_report, names)
    handler_report = await execute(apache.build_handler(plans, notified))
    return {
        "reports": reports,
        "plans": plans,
        "versions": versions,
        "notified": notified,
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


async def test_first_pass_installs_and_configures_apache(rig):
    inventory, hosts = rig
    result = await migrate(inventory, hosts)
    host = hosts["ubuntu"]

    # The packages are installed and the cache refresh ran.
    assert "apache2" in host.apt_packages
    assert "apache2-utils" in host.apt_packages
    assert host.apt_update_stamp is not None

    # The probe ran once, and its version selected the 2.4 variable table.
    assert host.ran_shells == [PROBE]
    assert result["versions"] == {"ubuntu": "2.4.58"}
    assert result["plans"]["ubuntu"]["default_vhost_filename"] == "000-default.conf"

    # ports.conf: the Listen line replaced, the mode converged.
    assert host.remote_files[PORTS_CONF] == b"Listen 8080\n"
    assert host.file_stats[PORTS_CONF]["mode"] == 644

    # The mod/conf symlinks: enabled created, disabled removed.
    assert host.links[f"{MODS_ENABLED}/rewrite.load"] == f"{MODS_AVAILABLE}/rewrite.load"
    assert host.links[f"{MODS_ENABLED}/ssl.load"] == f"{MODS_AVAILABLE}/ssl.load"
    assert f"{MODS_ENABLED}/autoindex.load" not in host.links
    assert host.links[f"{CONF_ENABLED}/security.conf"] == f"{CONF_AVAILABLE}/security.conf"

    # The rendered vhosts file, with the role's ownership and mode.
    content = host.remote_files[VHOSTS_FILE].decode()
    assert content.startswith("DirectoryIndex index.php index.html\n")
    assert "<VirtualHost *:8080>" in content
    assert "ServerName example.com" in content
    assert "ServerAlias www.example.com" in content
    assert 'DocumentRoot "/var/www/example"' in content
    assert "AllowOverride All" in content
    assert "Options -Indexes +FollowSymLinks" in content
    assert "Require all granted" in content
    # extra_parameters is indented by the template's indent(width=2, first=True).
    assert "  ErrorLog /var/log/apache2/example-error.log" in content
    assert "  CustomLog /var/log/apache2/example-access.log combined" in content
    assert host.file_stats[VHOSTS_FILE] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(content.encode()),
    }
    assert host.links[VHOSTS_LINK] == VHOSTS_FILE

    # The default vhost is gone, the service is running and enabled.
    assert DEFAULT_VHOST_LINK not in host.links
    assert host.services["apache2.service"] == "active"
    assert "apache2.service" in host.enabled_services

    # Every notifying task changed, so the restart handler fired.
    assert result["notified"] == frozenset({"ubuntu"})
    assert any(
        entry.name == "Restart apache2 (handler)" and entry.changed
        for entry in result["handler_report"].log
    )


async def test_non_ubuntu_host_is_never_touched(rig):
    inventory, hosts = rig
    result = await migrate(inventory, hosts)

    skipped = hosts["web1"]
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}
    assert skipped.links == {}
    assert skipped.services == {}
    assert skipped.ran_shells == []
    for report in result["reports"]:
        for entry in report.log:
            if entry.host == "web1":
                assert entry.skipped
                assert not entry.changed


async def test_handler_honours_the_restart_state(rig):
    """apache_restart_state selects the handler's verb, per host."""
    inventory = make_inventory(apache_restart_state="reloaded")
    hosts = make_hosts(inventory)
    result = await migrate(inventory, hosts)

    assert result["notified"] == frozenset({"ubuntu"})
    assert any(
        entry.name == "Reload apache2 (handler)" and entry.changed
        for entry in result["handler_report"].log
    )
    assert not any(
        entry.name == "Restart apache2 (handler)" for entry in result["handler_report"].log
    )


# --- the second pass is a no-op ----------------------------------------------


async def test_second_pass_changes_nothing_and_queues_no_handler(rig):
    inventory, hosts = rig
    first = await migrate(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    # No settle() bridge is needed: the simulation's mutators maintain every
    # seam the second pass reads (apt stamp, files, links, service state).
    second = await migrate(inventory, hosts)

    assert changed_entries(second) == []
    assert second["notified"] == frozenset()
    # The handler deploy still runs, but it holds no request for any host:
    # nothing is restarted on a converged machine.
    assert not any(entry.changed for entry in second["handler_report"].log)
    assert hosts["ubuntu"].ran_shells == [PROBE, PROBE]


async def test_dry_preview_mutates_nothing(rig):
    """``dry=True`` stops after prepare, so it must not touch the target."""
    inventory, hosts = rig
    host = hosts["ubuntu"]
    before_files = dict(host.remote_files)
    before_links = dict(host.links)
    before_services = dict(host.services)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    settings = apache.role_settings(host)
    plans = {host.name: apache.configure_plan(settings, "2.4.58", ())}
    await run(inventory, apache.deploy_install, host_factory=factory, dry=True)
    await run(inventory, apache.build_configure(plans), host_factory=factory, dry=True)

    assert host.remote_files == before_files
    assert host.links == before_links
    assert host.services == before_services
    assert host.apt_packages == {}


# --- the SSL branch -----------------------------------------------------------


@pytest.mark.parametrize("certificate_present", [True, False])
async def test_ssl_vhosts_are_gated_on_certificate_existence(certificate_present):
    """The stat snapshot gates each SSL vhost when ignore_missing is false."""
    inventory = make_inventory(
        apache_ignore_missing_ssl_certificate=False,
        apache_vhosts_ssl=[
            {
                "servername": "secure.example.com",
                "documentroot": "/var/www/secure",
                "certificate_file": CERT_FILE,
                "certificate_key_file": KEY_FILE,
            }
        ],
    )
    hosts = make_hosts(inventory)
    if certificate_present:
        hosts["ubuntu"].existing_files.add(CERT_FILE)

    result = await migrate(inventory, hosts)
    content = hosts["ubuntu"].remote_files[VHOSTS_FILE].decode()

    assert result["plans"]["ubuntu"]["vhosts_content"] == content
    if certificate_present:
        assert "<VirtualHost *:443>" in content
        assert "SSLEngine on" in content
        assert "SSLCompression off" in content  # the 2.4-only line
        assert f"SSLCertificateFile {CERT_FILE}" in content
        assert f"SSLCertificateKeyFile {KEY_FILE}" in content
        assert "SSLProtocol All -SSLv2 -SSLv3" in content
    else:
        assert "<VirtualHost *:443>" not in content
        assert "SSLEngine" not in content

    # Either way the whole sequence converges on a second pass.
    second = await migrate(inventory, hosts)
    assert changed_entries(second) == []
    assert second["notified"] == frozenset()


# --- the pure helpers, unit-tested directly -----------------------------------


def test_lineinfile_transform_matches_the_module_and_is_a_fixpoint():
    transform = apache.lineinfile_transform(r"^Listen ", "Listen 8080")
    cases = {
        None: "Listen 8080\n",  # absent file: created
        "": "Listen 8080\n",
        "Listen 80\n": "Listen 8080\n",  # regexp match replaced
        "Listen 8080\n": "Listen 8080\n",  # exact line present: verbatim
        "Listen 80\nListen 443\n": "Listen 80\nListen 8080\n",  # LAST match
        "# a comment\n": "# a comment\nListen 8080\n",  # no match: appended
    }
    for current, expected in cases.items():
        assert transform(current) == expected
        assert transform(transform(current)) == transform(current)


def test_parse_apache_version_reads_the_roles_field():
    assert apache.parse_apache_version(VERSION_STDOUT) == "2.4.58"
    assert apache.parse_apache_version("Server version: Apache/2.2.22 (Debian)") == "2.2.22"
    with pytest.raises(ValueError, match="cannot parse Apache version"):
        apache.parse_apache_version("not a version banner")


def test_ports_items_follow_the_version_tables():
    settings = {"listen_ip": "*", "listen_port": 8080}
    assert apache.ports_items(settings, "2.4") == ((r"^Listen ", "Listen 8080"),)
    settings["listen_ip"] = "192.0.2.10"
    assert apache.ports_items(settings, "2.4") == (
        (r"^Listen ", "Listen 192.0.2.10:8080"),
    )
    assert apache.ports_items(settings, "2.2") == (
        (r"^Listen ", "Listen 8080"),
        (r"^#?NameVirtualHost ", "NameVirtualHost 192.0.2.10:8080"),
    )


def test_configure_plan_rejects_an_unsupported_version():
    inventory = make_inventory()
    host = SimulatedHost.from_inventory(inventory[0])
    settings = apache.role_settings(host)
    with pytest.raises(ValueError, match="unsupported Apache version"):
        apache.configure_plan(settings, "2.0.65", ())