# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``apache`` role (design doc section 17.7, T5).

The role is library code now, so it is imported (``from reemote.roles import
apache``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, apache(v), host_factory=...)``. The old example's
harness -- import the script by path, gather snapshots, freeze a ``plans`` dict
keyed by host name, call ``run()`` three times -- is gone: the barriers inside
the body are the snapshot points and the driver runs them, so a test only runs
the role.

Two assertions carry the migration's claims, exactly as in the
``tests/roles/test_repo_epel.py`` precedent:

* **convergence** -- run the role, then run it again against the same persistent
  hosts, and the second pass reports no change anywhere. Because the rerun
  re-derives the probe and the certificate reads through ``run_waves``, this
  proves the barriers are honest rather than that a frozen plan was reused.
* **handler gating** -- the ``Restart apache2 (handler)`` line exists only on the
  pass whose ``checkpoint("configure done")`` found a changed notifying request.
  The converged pass has no handler record at all, where the old example still
  ran a third deploy that happened to hold no request.

Seeding choices, none of them a bridge:

* the version probe rides the first-class action seam (issue #16): ``seed()``
  plants the canned ``apache2 -v`` record in ``host.shell_results`` and the
  body's ``act`` barrier reads it through ``SimulatedHost._run_shell``.
  ``host.ran_shells`` is what the exactly-once assertions read -- the point of
  making the probe a barrier is that it can no longer run before the install
  applied.
* the simulated apt install records package names only, so ``ports.conf``, the
  default-vhost symlink and the ``/etc/apache2`` tree are seeded as the
  package's post-install state: in a real run the configure wave follows the
  install wave, when a machine has them.
* no between-pass bridge is needed at all. Every store the role touches (the
  apt update stamp, ``remote_files``/``file_stats``, ``links``/``link_stats``,
  the systemd unit state) is maintained by the simulation's own mutators, which
  is why the second pass converges instead of churning.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from reemote import run_waves
from reemote.roles import ApacheOutputs, ApacheVars, apache
from reemote.roles.apache import (
    DEBIAN_PACKAGES,
    ApachePlan,
    SslVHost,
    VHost,
    configure_plan,
    lineinfile_transform,
    notifying_names,
    parse_apache_version,
    ports_items,
    render_vhosts,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from reemote.stages import scoped
from tests.simulated_host import SimulatedHost

PROBE = "/usr/sbin/apache2 -v"
VERSION_STDOUT = (
    "Server version: Apache/2.4.58 (Ubuntu)\n"
    "Server built:   2026-01-01T00:00:00\n"
)
VERSION_22_STDOUT = (
    "Server version: Apache/2.2.22 (Debian)\nServer built: 2012-01-01\n"
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

INSTALL_NAMES = (
    "Update apt cache",
    "Ensure Apache is installed on Debian",
)
#: The configure wave of the default inventory: ``apache_listen_port`` 8080,
#: two mods enabled, one disabled, one conf enabled, vhosts created, the default
#: vhost removed, then the (non-notifying) service task.
CONFIGURE_NAMES = (
    "Configure Apache ports (^Listen )",
    "Set Apache ports.conf mode",
    "Enable Apache mod rewrite",
    "Enable Apache mod ssl",
    "Disable Apache mod autoindex",
    "Enable Apache config security",
    "Add apache vhosts configuration",
    "Add vhost symlink in sites-enabled",
    "Remove default vhost in sites-enabled",
    "Ensure Apache has selected state and enabled on boot",
)
#: The one configure request that no-ops on a stock seeded host: ``ports.conf``
#: already exists at mode 0644, so the paired chmod has nothing to do.
UNCHANGED_FIRST_PASS = ("Set Apache ports.conf mode",)
HANDLER_NAME = "Restart apache2 (handler)"

SSL_VHOST = {
    "servername": "secure.example.com",
    "documentroot": "/var/www/secure",
    "certificate_file": CERT_FILE,
    "certificate_key_file": KEY_FILE,
}


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host to be skipped.

    The role variables ride on the entry as ``apache_``-prefixed extra keys,
    which is what ``resolve_vars(ApacheVars, "apache_", inventory)`` binds, so a
    test changes a variable exactly the way an operator does. The base entry
    exercises the non-default branches: a custom port, the default-vhost
    removal, a disabled mod, an enabled conf, and a vhost with the optional
    alias/documentroot/extra-parameters fields.
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

    See the module docstring: the package's own files are seeded as its
    post-install state, because the simulation installs names, not payloads.
    """
    host.apt_packages = {}
    host.apt_update_stamp = None
    host.remote_files = {PORTS_CONF: b"Listen 80\n"}
    host.existing_files = {PORTS_CONF}
    host.file_stats = {
        PORTS_CONF: {"user": "root", "group": "root", "mode": 644, "size": 10}
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
    host.shell_results[PROBE] = {
        "exit_status": 0,
        "stdout": VERSION_STDOUT,
        "stderr": "",
    }


def make_hosts(
    inventory: list[dict[str, Any]],
    *,
    cert_present: bool = False,
    version_stdout: str = VERSION_STDOUT,
    probe_exit: int = 0,
    probe_stderr: str = "",
) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded target.

    Only the Ubuntu entry is seeded, so the RedHat host keeps its stock empty
    stores and an accidental request on it is caught by the untouched-store
    assertions. ``cert_present`` plants the SSL vhost's certificate, which is the
    only fact the role reads that changes what it writes, and the two probe
    arguments steer the version barrier -- its banner selects the variable table,
    its exit status is the body's first gate.
    """
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            seed(host)
            host.shell_results[PROBE] = {
                "exit_status": probe_exit,
                "stdout": version_stdout,
                "stderr": probe_stderr,
            }
            if cert_present:
                host.existing_files.add(CERT_FILE)
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
    v = resolve_vars(ApacheVars, "apache_", inventory)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    report, _hosts = await run_waves(
        inventory, apache(v), host_factory=factory, dry=dry
    )
    return report


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


def host_records(report: Any, host: str) -> list[Any]:
    """Every record (request step or barrier) attributed to *host*."""
    return [record for record in report.records if record.host == host]


def barriers(report: Any, host: str = "ubuntu") -> list[Any]:
    """The barrier objects *host* parked on, in wave order."""
    return [record.barrier for record in host_records(report, host) if record.barrier]


def waves(report: Any, host: str = "ubuntu") -> list[int]:
    """The distinct wave numbers *host* emitted records in."""
    return sorted({record.wave for record in host_records(report, host)})


def request_kwargs(report: Any, name: str) -> dict:
    """The forwarded keyword arguments of the one planned request called *name*."""
    record = next(
        record
        for record in report.records
        if record.planned is not None and record.planned.request.name == name
    )
    return record.planned.request.kwargs


def vhosts_text(host: SimulatedHost) -> str:
    """What the role rendered into ``sites-available/vhosts.conf``."""
    return host.remote_files[VHOSTS_FILE].decode("utf-8")


# --- the first pass really deploys the role -----------------------------------


async def test_first_pass_installs_and_configures_apache():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # The packages are installed and the cache refresh ran.
    assert "apache2" in host.apt_packages
    assert "apache2-utils" in host.apt_packages
    assert host.apt_update_stamp is not None

    # ports.conf: the Listen line replaced through the frozen transform.
    assert host.remote_files[PORTS_CONF] == b"Listen 8080\n"
    assert host.file_stats[PORTS_CONF]["mode"] == 644

    # The mod/conf symlinks: enabled created, disabled removed.
    assert host.links[f"{MODS_ENABLED}/rewrite.load"] == f"{MODS_AVAILABLE}/rewrite.load"
    assert host.links[f"{MODS_ENABLED}/ssl.load"] == f"{MODS_AVAILABLE}/ssl.load"
    assert f"{MODS_ENABLED}/autoindex.load" not in host.links
    assert host.links[f"{CONF_ENABLED}/security.conf"] == f"{CONF_AVAILABLE}/security.conf"

    # The rendered vhosts file, with the role's ownership and mode.
    content = vhosts_text(host)
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

    # Every notifying task changed, so the handler fired.
    assert entry_named(report, HANDLER_NAME).changed
    assert [e.name for e in changed_entries(report)] == [
        *INSTALL_NAMES,
        *[name for name in CONFIGURE_NAMES if name not in UNCHANGED_FIRST_PASS],
        HANDLER_NAME,
    ]


async def test_one_stream_replaces_the_three_deploy_split():
    """Install, probe, configure, checkpoint and handler share one pass.

    The old example needed three ``run()`` calls and two Python-side snapshots
    between them. Here the only barriers are the probe (which *is* the reason
    install and configure cannot share a wave) and the checkpoint that reads the
    configure wave back -- so three waves, not three deploys.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert [type(b).__name__ for b in barriers(report)] == [
        "ActionRequest",
        "CheckpointRequest",
    ]
    assert waves(report) == [0, 1, 2]

    ordered = [entry.name for entry in report.merged().log if entry.host == "ubuntu"]
    assert ordered == [*INSTALL_NAMES, *CONFIGURE_NAMES, HANDLER_NAME]


async def test_the_probe_barrier_closes_the_install_wave():
    """The barrier makes the version read happen after the packages applied.

    Under the old three-deploy split this was true by construction; as a stream
    it is true only because the ``act`` barrier ends the wave. The simulated
    version banner exists for the whole run, so the ordering proof is the record
    sequence: the two install requests sit in wave 0 *with* the barrier, and the
    banner is what the body received back.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    records = host_records(report, "ubuntu")
    probe = next(record for record in records if record.barrier is not None)
    install_waves = {
        record.wave
        for record in records
        if record.planned is not None
        and record.planned.request.name in INSTALL_NAMES
    }
    assert install_waves == {0}
    assert probe.wave == 0
    assert probe.outcome["stdout"] == VERSION_STDOUT
    # The configure requests are in a later wave than the probe, never its own.
    configure_waves = {
        record.wave
        for record in records
        if record.planned is not None
        and record.planned.request.name in CONFIGURE_NAMES
    }
    assert configure_waves == {1}


# --- the family guard ---------------------------------------------------------


async def test_non_ubuntu_host_emits_nothing():
    """The family guard is the body's opening ``if``, not a ``when`` on each op.

    A host outside the ``"Ubuntu"`` family returns before it yields anything, so
    it records no request and no barrier -- where the old example logged each of
    its requests on that host as ``[skipped: when guard ...]``.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    skipped = hosts["web1"]
    assert host_records(report, "web1") == []
    assert [e for e in report.merged().log if e.host == "web1"] == []
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}
    assert skipped.links == {}
    assert skipped.services == {}
    assert skipped.ran_shells == []


# --- the handler --------------------------------------------------------------


@pytest.mark.parametrize(
    ("restart_state", "expected", "reports_change"),
    [
        ("restarted", "Restart apache2 (handler)", True),
        ("reloaded", "Reload apache2 (handler)", True),
        ("started", "Start apache2 (handler)", False),
    ],
)
async def test_handler_verb_follows_the_restart_state(
    restart_state, expected, reports_change
):
    """``apache_restart_state`` selects the verb, and only that verb, per host.

    The example partitioned the notified set by action and composed one handler
    deploy per verb; a per-host body has exactly one state to honour. ``restarted``
    and ``reloaded`` are stateless and always report a change, faithful to
    Ansible's service module; ``started`` is fact-gated, and the configure wave's
    own service task already has the unit running, so it queues the verb and the
    verb no-ops.
    """
    inventory = make_inventory(apache_restart_state=restart_state)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert entry_named(report, expected).changed is reports_change
    handler_names = [
        entry.name
        for entry in report.merged().log
        if entry.host == "ubuntu" and entry.name.endswith("(handler)")
    ]
    assert handler_names == [expected]


async def test_only_a_changed_notifying_request_queues_the_handler():
    """The checkpoint's gate: content equal, mode drifted, one task changes.

    ``files.transform`` is content-idempotent, so the ports task no-ops and only
    the companion ``files.file`` chmod reports a change -- yet that one name is
    in the role's notifying set, so the handler still fires. It is the smallest
    possible proof of the notification gate.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    # Converge first, then drift the mode alone: content already matches, so the
    # content-idempotent transform no-ops and only the chmod changes.
    await run_role(inventory, hosts)
    host = hosts["ubuntu"]
    host.file_stats[PORTS_CONF]["mode"] = 600

    report = await run_role(inventory, hosts)

    assert not entry_named(report, "Configure Apache ports (^Listen )").changed
    assert entry_named(report, "Set Apache ports.conf mode").changed
    assert host.file_stats[PORTS_CONF]["mode"] == 644
    assert entry_named(report, HANDLER_NAME).changed
    assert "configure done → 1 host notified" in report.tree()

    third = await run_role(inventory, hosts)
    assert changed_entries(third) == []


async def test_converged_host_gets_no_handler_record():
    """No changed notifying entry means the handler wave holds nothing.

    ``service_handler`` yields no request for a host outside the notified set, so
    the wave is not even appended -- the old example ran a third deploy that
    produced an empty log instead.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    second = await run_role(inventory, hosts)

    assert [type(b).__name__ for b in barriers(second)] == [
        "ActionRequest",
        "CheckpointRequest",
    ]
    assert waves(second) == [0, 1]
    assert [e.name for e in second.merged().log if e.name.endswith("(handler)")] == []
    assert "configure done → 0 hosts notified" in second.tree()


# --- convergence --------------------------------------------------------------


async def test_second_pass_changes_nothing():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert second.tree() == CONVERGED_TREE
    # The probe still runs once per pass: a barrier is a real read, not a cached
    # plan value from the first pass.
    assert hosts["ubuntu"].ran_shells == [PROBE, PROBE]


async def test_dry_preview_stops_at_the_probe_barrier():
    """``dry`` is a one-wave preview: it plans, parks, and never runs a barrier.

    The install requests get plan lines, the probe is ``unknown until run``, and
    the body stops there -- no log entry, no command, nothing applied. A
    converged rerun is the honest preview (§16.6).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    report = await run_role(inventory, hosts, dry=True)

    assert report.merged().log == []
    assert host.ran_shells == []
    assert host.apt_packages == {}
    assert host.remote_files[PORTS_CONF] == b"Listen 80\n"
    assert VHOSTS_FILE not in host.remote_files
    assert DEFAULT_VHOST_LINK in host.links
    assert host.services == {}
    assert report.tree() == DRY_PREVIEW_TREE


# --- the SSL branch -----------------------------------------------------------


async def test_ssl_certificate_present_renders_the_443_block():
    """One certificate read is one extra wave: four waves for a single SSL vhost."""
    inventory = make_inventory(
        apache_ignore_missing_ssl_certificate=False,
        apache_vhosts_ssl=[SSL_VHOST],
    )
    hosts = make_hosts(inventory, cert_present=True)
    report = await run_role(inventory, hosts)

    assert [type(b).__name__ for b in barriers(report)] == [
        "ActionRequest",
        "FactRequest",
        "CheckpointRequest",
    ]
    assert waves(report) == [0, 1, 2, 3]

    content = vhosts_text(hosts["ubuntu"])
    assert "<VirtualHost *:443>" in content
    assert "SSLEngine on" in content
    assert "SSLCompression off" in content  # the 2.4-only line
    assert f"SSLCertificateFile {CERT_FILE}" in content
    assert f"SSLCertificateKeyFile {KEY_FILE}" in content
    assert "SSLProtocol All -SSLv2 -SSLv3" in content
    assert 'DocumentRoot "/var/www/secure"' in content
    # The plain vhost is still there, first: the loop order is the template's.
    assert content.index("<VirtualHost *:8080>") < content.index("<VirtualHost *:443>")

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    # The certificate read costs a wave on every pass, converged or not; the
    # handler wave is the one that disappears.
    assert waves(second) == [0, 1, 2]


async def test_ssl_vhost_is_gated_when_the_certificate_is_missing():
    """The stat answer the role registered is now the barrier's own value."""
    inventory = make_inventory(
        apache_ignore_missing_ssl_certificate=False,
        apache_vhosts_ssl=[SSL_VHOST],
    )
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert [type(b).__name__ for b in barriers(report)][1] == "FactRequest"
    content = vhosts_text(hosts["ubuntu"])
    assert "<VirtualHost *:443>" not in content
    assert "SSLEngine" not in content
    assert "secure.example.com" not in content

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_ignore_missing_ssl_certificate_skips_the_gate_not_the_read():
    """The default renders the vhost although the certificate is absent.

    The role stats every SSL vhost unconditionally -- ``ignore_missing`` is a
    template-side ``or``, not a guard on the read -- so the wave count is
    unchanged. That is faithful, and worth pinning: the extra wave is the cost of
    the loop, not of the gate.
    """
    inventory = make_inventory(apache_vhosts_ssl=[SSL_VHOST])
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert len(barriers(report)) == 3
    content = vhosts_text(hosts["ubuntu"])
    assert "<VirtualHost *:443>" in content
    assert f"SSLCertificateFile {CERT_FILE}" in content


async def test_two_certificates_cost_two_waves_and_stay_aligned():
    """Per-certificate waves, and the exists list keeps the vhost order."""
    inventory = make_inventory(
        apache_ignore_missing_ssl_certificate=False,
        apache_vhosts_ssl=[
            SSL_VHOST,
            {
                **SSL_VHOST,
                "servername": "other.example.com",
                "certificate_file": "/etc/ssl/certs/other.pem",
            },
        ],
    )
    hosts = make_hosts(inventory, cert_present=True)
    report = await run_role(inventory, hosts)

    assert [type(b).__name__ for b in barriers(report)] == [
        "ActionRequest",
        "FactRequest",
        "FactRequest",
        "CheckpointRequest",
    ]
    assert waves(report) == [0, 1, 2, 3, 4]

    content = vhosts_text(hosts["ubuntu"])
    assert content.count("<VirtualHost *:443>") == 1  # only the present certificate
    assert "ServerName secure.example.com" in content
    assert "ServerName other.example.com" not in content


# --- the version tables -------------------------------------------------------


async def test_apache_22_selects_the_22_table():
    """The probed minor version picks the variable table inside the body.

    Two ``ports.conf`` items instead of one, ``Order allow,deny`` instead of
    ``Require all granted``, no ``SSLCompression off``, and the default vhost
    file without its ``.conf`` suffix.
    """
    inventory = make_inventory(apache_vhosts_ssl=[SSL_VHOST])
    hosts = make_hosts(inventory, version_stdout=VERSION_22_STDOUT)
    report = await run_role(inventory, hosts)

    ordered = [entry.name for entry in report.merged().log if entry.host == "ubuntu"]
    assert "Configure Apache ports (^#?NameVirtualHost )" in ordered
    assert hosts["ubuntu"].remote_files[PORTS_CONF] == (
        b"Listen 8080\nNameVirtualHost *:8080\n"
    )

    content = vhosts_text(hosts["ubuntu"])
    assert "Order allow,deny" in content
    assert "Allow from all" in content
    assert "Require all granted" not in content
    assert "<VirtualHost *:443>" in content  # ignore_missing is the default
    assert "SSLCompression off" not in content
    # The 2.2 table's default vhost filename is what the removal targets.
    removal = request_kwargs(report, "Remove default vhost in sites-enabled")
    assert removal["path"] == f"{SITES_ENABLED}/000-default"

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_unsupported_version_raises_before_configuring():
    inventory = make_inventory()
    hosts = make_hosts(inventory, version_stdout="Server version: Apache/2.0.65 (Debian)\n")

    with pytest.raises(ValueError, match="unsupported Apache version"):
        await run_role(inventory, hosts)

    host = hosts["ubuntu"]
    assert host.remote_files[PORTS_CONF] == b"Listen 80\n"
    assert VHOSTS_FILE not in host.remote_files
    assert host.services == {}
    # The install wave had already applied when the body gave up.
    assert "apache2" in host.apt_packages


async def test_probe_failure_raises_before_configuring():
    """A non-zero barrier exit is data, so the body turns it into the error.

    The old example checked this in ``main()`` between two deploys; the runner
    maps ``ValueError`` either way, which is why this is not a ``RuntimeError``.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory, probe_exit=1, probe_stderr="apache2: Syntax error on line 1")
    with pytest.raises(ValueError, match="Apache version probe failed"):
        await run_role(inventory, hosts)

    host = hosts["ubuntu"]
    assert host.ran_shells == [PROBE]
    assert host.remote_files[PORTS_CONF] == b"Listen 80\n"
    assert f"{MODS_ENABLED}/rewrite.load" not in host.links


async def test_malformed_probe_output_raises():
    inventory = make_inventory()
    hosts = make_hosts(inventory, version_stdout="Server version: Apache 2.4.58 (Ubuntu)\n")

    with pytest.raises(ValueError, match="cannot parse Apache version"):
        await run_role(inventory, hosts)


# --- the variables ------------------------------------------------------------


async def test_packages_variable_replaces_the_debian_pair():
    inventory = make_inventory(apache_packages=["apache2", "php"])
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert request_kwargs(report, INSTALL_NAMES[1])["packages"] == ("apache2", "php")


async def test_state_and_enabled_reach_the_service_task():
    inventory = make_inventory(apache_state="stopped", apache_enabled=False)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    kwargs = request_kwargs(report, "Ensure Apache has selected state and enabled on boot")
    assert kwargs["running"] is False
    assert kwargs["enabled"] is False
    assert kwargs["restarted"] is False
    assert kwargs["reloaded"] is False
    assert kwargs["service"] == "apache2"
    # The stop request ran in the configure wave, and the handler's restart
    # followed it in the next one -- the role's own ordering, in which a
    # notifying change queues ``restart apache`` after ``apache_state`` has had
    # its say, so the unit ends up active on a host that changed something.
    assert hosts["ubuntu"].enabled_services == set()
    assert hosts["ubuntu"].services["apache2.service"] == "active"
    assert entry_named(report, HANDLER_NAME).changed


async def test_create_vhosts_false_drops_the_notifying_pair():
    """No vhosts file, no symlink, and neither name in the notification set."""
    inventory = make_inventory(apache_create_vhosts=False)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert VHOSTS_FILE not in hosts["ubuntu"].remote_files
    ordered = [entry.name for entry in report.merged().log if entry.host == "ubuntu"]
    assert "Add apache vhosts configuration" not in ordered
    assert "Add vhost symlink in sites-enabled" not in ordered
    assert entry_named(report, HANDLER_NAME).changed  # the other tasks still notify

    plan = configure_plan(resolve_vars(ApacheVars, "apache_", inventory)["ubuntu"], "2.4.58", ())
    assert "Add apache vhosts configuration" not in notifying_names(plan)

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_custom_vhosts_template_is_read_controller_side(tmp_path):
    """``apache_vhosts_template`` names a file on the controller, not the host."""
    template = tmp_path / "vhosts.conf.j2"
    template.write_text(
        "Listen {{ apache_listen_port }} for {{ apache_vhosts[0].servername }}\n",
        encoding="utf-8",
    )
    inventory = make_inventory(apache_vhosts_template=str(template))
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    assert vhosts_text(hosts["ubuntu"]) == "Listen 8080 for example.com\n"


async def test_inert_variables_still_bind():
    """``apache_enablerepo``/``apache_ssl_no_log`` are carried and do nothing.

    Pinned as equality against the default tree: an inventory written for the
    role keeps working, and the two knobs provably change no request.
    """
    inventory = make_inventory(apache_enablerepo="extra", apache_ssl_no_log=False)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert report.tree() == FIRST_PASS_TREE


# --- the pure half, unit-tested directly --------------------------------------


def test_lineinfile_transform_matches_the_module_and_is_a_fixpoint():
    transform = lineinfile_transform(r"^Listen ", "Listen 8080")
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
    assert parse_apache_version(VERSION_STDOUT) == "2.4.58"
    assert parse_apache_version(VERSION_22_STDOUT) == "2.2.22"
    with pytest.raises(ValueError, match="cannot parse Apache version"):
        parse_apache_version("not a version banner")


def test_ports_items_follow_the_version_tables():
    v = ApacheVars(listen_ip="*", listen_port=8080)
    assert ports_items(v, "2.4") == ((r"^Listen ", "Listen 8080"),)

    v = ApacheVars(listen_ip="192.0.2.10", listen_port=8080)
    assert ports_items(v, "2.4") == ((r"^Listen ", "Listen 192.0.2.10:8080"),)
    assert ports_items(v, "2.2") == (
        (r"^Listen ", "Listen 8080"),
        (r"^#?NameVirtualHost ", "NameVirtualHost 192.0.2.10:8080"),
    )


def test_configure_plan_freezes_the_reads_and_is_hashable():
    v = ApacheVars(
        listen_port=8080,
        mods_disabled=("autoindex",),
        conf_enabled=("security",),
        remove_default_vhost=True,
    )
    plan = configure_plan(v, "2.4.58", (False,))

    assert isinstance(plan, ApachePlan)
    assert plan.service == "apache2"
    assert plan.server_root == "/etc/apache2"
    assert plan.default_vhost_filename == "000-default.conf"
    assert [(regexp, line) for regexp, line, _ in plan.ports] == [
        (r"^Listen ", "Listen 8080")
    ]
    # The transform is a stable callable built once, so a request carrying it
    # hashes identically across the two phases of a run.
    transform = plan.ports[0][2]
    assert transform("Listen 80\n") == "Listen 8080\n"
    assert "<VirtualHost *:8080>" in plan.vhosts_content
    assert hash(plan)

    plan22 = configure_plan(v, "2.2.22", ())
    assert plan22.default_vhost_filename == "000-default"
    assert len(plan22.ports) == 2


def test_configure_plan_rejects_an_unsupported_version():
    with pytest.raises(ValueError, match="unsupported Apache version"):
        configure_plan(ApacheVars(), "2.0.65", ())


def test_notifying_names_enumerate_the_plan():
    plan = configure_plan(
        ApacheVars(
            mods_enabled=("rewrite", "ssl"),
            mods_disabled=("autoindex",),
            conf_enabled=("security",),
            conf_disabled=("timeout",),
            remove_default_vhost=True,
        ),
        "2.4.58",
        (),
    )

    assert notifying_names(plan) == (
        "Set Apache ports.conf mode",
        "Configure Apache ports (^Listen )",
        "Enable Apache mod rewrite",
        "Enable Apache mod ssl",
        "Disable Apache mod autoindex",
        "Enable Apache config security",
        "Disable Apache config timeout",
        "Add apache vhosts configuration",
        "Add vhost symlink in sites-enabled",
        "Remove default vhost in sites-enabled",
    )
    # The role carries no notify on the final service task.
    assert "Ensure Apache has selected state and enabled on boot" not in (
        notifying_names(plan)
    )


def test_render_vhosts_dumps_optional_keys_as_undefined():
    """An unset optional key must stay undefined, not render as ``None``."""
    v = ApacheVars(vhosts=(VHost(servername="bare.example.com"),), vhosts_ssl=())
    content = render_vhosts(v, "2.4", ())

    assert "ServerName bare.example.com" in content
    assert "ServerAlias" not in content
    assert 'DocumentRoot ""' not in content
    assert "None" not in content
    # No documentroot: the <Directory> block is skipped entirely.
    assert "<Directory" not in content


def test_render_vhosts_defaults_follow_the_version():
    v = ApacheVars(
        vhosts=(VHost(servername="a.example.com", documentroot="/var/www/a"),),
        vhosts_ssl=(),
    )

    assert "Require all granted" in render_vhosts(v, "2.4", ())
    assert "Order allow,deny" in render_vhosts(v, "2.2", ())


# --- the variables model ------------------------------------------------------


def test_packages_default_to_the_debian_pair():
    assert ApacheVars().packages is None
    # The body's ``v.packages or DEBIAN_PACKAGES`` fallback, as the role's
    # ``set_fact`` did for an undefined apache_packages.
    assert DEBIAN_PACKAGES == ("apache2", "apache2-utils")


def test_vhosts_sequence_binds_from_lists():
    v = resolve_vars(
        ApacheVars,
        "apache_",
        make_inventory(
            apache_packages=["apache2"],
            apache_mods_enabled=["rewrite"],
            apache_vhosts=[{"servername": "a.example.com"}],
        ),
    )["ubuntu"]

    assert v.packages == ("apache2",)
    assert v.mods_enabled == ("rewrite",)
    assert v.vhosts == (VHost(servername="a.example.com"),)


def test_vhost_requires_a_servername():
    with pytest.raises(ValidationError, match="servername"):
        VHost.model_validate({"documentroot": "/var/www/html"})
    with pytest.raises(ValueError, match="needs a servername"):
        VHost(servername="")


def test_ssl_vhost_requires_both_certificate_paths():
    with pytest.raises(ValidationError, match="certificate_key_file"):
        SslVHost(servername="s.example.com", certificate_file=CERT_FILE)
    with pytest.raises(ValidationError, match="certificate path"):
        SslVHost(
            servername="s.example.com", certificate_file="", certificate_key_file=KEY_FILE
        )
    # A vhost may carry anything else the role's template learns to read.
    extra = SslVHost(
        servername="s.example.com",
        certificate_file=CERT_FILE,
        certificate_key_file=KEY_FILE,
        certificate_chain_file="/etc/ssl/certs/chain.pem",
        somesuch="kept",
    )
    assert extra.somesuch == "kept"  # type: ignore[attr-defined]


def test_vars_reject_bad_enum_values():
    with pytest.raises(ValidationError, match="state"):
        ApacheVars(state="enabled")
    with pytest.raises(ValidationError, match="restart_state"):
        ApacheVars(restart_state="rebooted")
    with pytest.raises(ValidationError, match="packages_state"):
        ApacheVars(packages_state="installed")
    with pytest.raises(ValidationError, match="listen_port"):
        ApacheVars(listen_port="not-a-port-not-a-number")


def test_vars_are_frozen_and_reject_unknown_fields():
    v = ApacheVars()
    with pytest.raises(ValidationError):
        v.listen_port = 8080  # type: ignore[misc]
    with pytest.raises(ValidationError):
        # The prefixed inventory spelling is not a field name.
        ApacheVars(apache_listen_port=8080)


def test_inventory_binds_the_prefixed_variables():
    v = resolve_vars(ApacheVars, "apache_", make_inventory(apache_listen_port=8443))

    assert v["ubuntu"].listen_port == 8443
    # The other role's host keeps the defaults: no apache_ keys, nothing bound.
    assert v["web1"] == ApacheVars()


def test_prefixed_typo_is_reported_not_ignored():
    inventory = make_inventory(apache_listen_prot=8080)
    hosts = make_hosts(inventory)

    with pytest.raises(RoleVarsError, match="apache_listen_prot"):
        resolve_vars(ApacheVars, "apache_", inventory)
    assert hosts["ubuntu"].apt_packages == {}


# --- composition --------------------------------------------------------------


async def test_composing_role_captures_the_outputs():
    """``yield from scoped(...)`` returns the child's outputs to the parent.

    Both paths matter: the target host publishes the probed version, the skipped
    host still publishes an ``ApacheOutputs`` -- an empty version -- because a
    composing role reads a return value, not a log.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    v = resolve_vars(ApacheVars, "apache_", inventory)
    captured: dict[str, ApacheOutputs] = {}

    def parent(host):
        outputs = yield from scoped("apache", apache(v))(host)
        captured[host.name] = outputs

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    report, _hosts = await run_waves(inventory, parent, host_factory=factory)

    assert captured["ubuntu"] == ApacheOutputs(version="2.4.58")
    assert captured["web1"] == ApacheOutputs(version="")
    assert captured["ubuntu"].service == "apache2"
    # The scope label reaches the report tree, the log names do not change.
    assert "apache" in report.tree()
    assert entry_named(report, HANDLER_NAME).changed


# --- the wave tree (design doc section 7.2) -----------------------------------

# Pinned from a real simulated run (``pytest tests/roles/test_apache.py -k
# snapshot`` prints the diff against them). The install spool directories are
# content-addressed, so the hashes are stable; the ``apache -v`` line carries the
# barrier's raw ``Result`` dict, which is why it is a long single line.
FIRST_PASS_TREE = """(root)
  wave 0
    ubuntu: Update apt cache         [changed: apt update]
        $ sudo apt-get update
    ubuntu: Ensure Apache is installed on Debian [changed: 2 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install apache2 apache2-utils
    barrier: apache -v → {'exit_status': 0, 'stdout': 'Server version: Apache/2.4.58 (Ubuntu)\\nServer built:   2026-01-01T00:00:00\\n', 'stderr': ''}
  wave 1
    ubuntu: Configure Apache ports (^Listen ) [changed: content /etc/apache2/ports.conf differs, will install]
        $ scp <files install> -> /tmp/reemote-install-098295c0a2f4f079/content
        $ sudo sh -c 'mkdir -p /etc/apache2 && cp /tmp/reemote-install-098295c0a2f4f079/content /etc/apache2/ports.conf && chmod 644 /etc/apache2/ports.conf && rm -rf /tmp/reemote-install-098295c0a2f4f079'
    ubuntu: Set Apache ports.conf mode [no change: file /etc/apache2/ports.conf already exists]
    ubuntu: Enable Apache mod rewrite [changed: /etc/apache2/mods-enabled/rewrite.load missing, will create]
        $ sudo ln -s /etc/apache2/mods-available/rewrite.load /etc/apache2/mods-enabled/rewrite.load
    ubuntu: Enable Apache mod ssl    [changed: /etc/apache2/mods-enabled/ssl.load missing, will create]
        $ sudo ln -s /etc/apache2/mods-available/ssl.load /etc/apache2/mods-enabled/ssl.load
    ubuntu: Disable Apache mod autoindex [changed: /etc/apache2/mods-enabled/autoindex.load present, will remove]
        $ sudo rm -f /etc/apache2/mods-enabled/autoindex.load
    ubuntu: Enable Apache config security [changed: /etc/apache2/conf-enabled/security.conf missing, will create]
        $ sudo ln -s /etc/apache2/conf-available/security.conf /etc/apache2/conf-enabled/security.conf
    ubuntu: Add apache vhosts configuration [changed: content /etc/apache2/sites-available/vhosts.conf is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-10050aa601b95bc6/content
        $ sudo sh -c 'mkdir -p /etc/apache2/sites-available && cp /tmp/reemote-install-10050aa601b95bc6/content /etc/apache2/sites-available/vhosts.conf && chown root:root /etc/apache2/sites-available/vhosts.conf && chmod 644 /etc/apache2/sites-available/vhosts.conf && rm -rf /tmp/reemote-install-10050aa601b95bc6'
    ubuntu: Add vhost symlink in sites-enabled [changed: /etc/apache2/sites-enabled/vhosts.conf missing, will create]
        $ sudo ln -s /etc/apache2/sites-available/vhosts.conf /etc/apache2/sites-enabled/vhosts.conf
    ubuntu: Remove default vhost in sites-enabled [changed: /etc/apache2/sites-enabled/000-default.conf present, will remove]
        $ sudo rm -f /etc/apache2/sites-enabled/000-default.conf
    ubuntu: Ensure Apache has selected state and enabled on boot [changed: apache2.service not running (None), will start; apache2.service not enabled, will enable]
        $ sudo systemctl start apache2.service
        $ sudo systemctl enable apache2.service
    barrier: configure done → 1 host notified
  wave 2
    ubuntu: Restart apache2 (handler) [changed: apache2.service already running; apache2.service will restart]
        $ sudo systemctl restart apache2.service"""

CONVERGED_TREE = """(root)
  wave 0
    ubuntu: Update apt cache         [no change: apt update skipped, cache is fresh]
    ubuntu: Ensure Apache is installed on Debian [no change: all packages present]
    barrier: apache -v → {'exit_status': 0, 'stdout': 'Server version: Apache/2.4.58 (Ubuntu)\\nServer built:   2026-01-01T00:00:00\\n', 'stderr': ''}
  wave 1
    ubuntu: Configure Apache ports (^Listen ) [no change: content already up to date (/etc/apache2/ports.conf)]
    ubuntu: Set Apache ports.conf mode [no change: file /etc/apache2/ports.conf already exists]
    ubuntu: Enable Apache mod rewrite [no change: link /etc/apache2/mods-enabled/rewrite.load already exists]
    ubuntu: Enable Apache mod ssl    [no change: link /etc/apache2/mods-enabled/ssl.load already exists]
    ubuntu: Disable Apache mod autoindex [no change: link /etc/apache2/mods-enabled/autoindex.load does not exist]
    ubuntu: Enable Apache config security [no change: link /etc/apache2/conf-enabled/security.conf already exists]
    ubuntu: Add apache vhosts configuration [no change: content already up to date (/etc/apache2/sites-available/vhosts.conf)]
    ubuntu: Add vhost symlink in sites-enabled [no change: link /etc/apache2/sites-enabled/vhosts.conf already exists]
    ubuntu: Remove default vhost in sites-enabled [no change: link /etc/apache2/sites-enabled/000-default.conf does not exist]
    ubuntu: Ensure Apache has selected state and enabled on boot [no change: apache2.service already running; apache2.service already enabled]
    barrier: configure done → 0 hosts notified"""

DRY_PREVIEW_TREE = """(root)
  wave 0
    ubuntu: Update apt cache         [changed: apt update]
    ubuntu: Ensure Apache is installed on Debian [changed: 2 package(s) to install]
    barrier: apache -v → unknown until run"""


async def test_tree_snapshot():
    """The whole run as one tree: install, probe, configure, checkpoint, handler.

    The second tree is what a rerun prints -- one wave fewer, no handler line --
    and it is the preview ``dry`` deliberately refuses to fake (§16.6).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)

    assert first.tree() == FIRST_PASS_TREE

    second = await run_role(inventory, hosts)
    assert second.tree() == CONVERGED_TREE
