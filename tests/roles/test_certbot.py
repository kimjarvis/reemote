# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``certbot`` role (design doc §17.5).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, certbot(v), host_factory=...)``. The factory is a plain
(non-``async``) function; the ``include_vars`` release dispatch is a per-host
``fact(LinuxDistribution)`` barrier and the domain probe / certificate create
are ``act(Shell)`` barriers inside the body.

The load-bearing assertion is the migration's idempotence claim: run the role,
then run it *again* against the same persistent hosts, and the second pass must
report no change anywhere and re-run no action beyond the probe (which
``changed_when: false`` marks as a read). The package is installed, the hook
files and webroot match, the certificate now exists (planted in-stream by
``CertbotSimulatedHost._run_shell``, the sanctioned postgresql/composer
precedent: a raw ``Shell`` action mutates no file store), the probe answers the
configured domains, and the cron entry is already in the table.

One simulation note: the probe's seeded ``stdout`` is what the whole pipeline
(``certificates | grep | cut``) would leave on stdout -- the leading space the
real ``cut -d':' -f2`` keeps after the colon is included, so ``parse_domains``
sees exactly what the role's cleanup chain saw.
"""

from typing import Any

import pytest

from reemote import CommandError, run_waves
from reemote.roles import Cert, CertbotVars, certbot
from reemote.roles.certbot import (
    CRON_NAME,
    DEFAULT_PACKAGE,
    LIVE_DIR,
    PACKAGE_BY_RELEASE,
    POST_HOOK_PATH,
    PRE_HOOK_PATH,
    cert_name,
    create_command,
    cron_privilege,
    desired_domains,
    domains_command,
    parse_domains,
    render_service_hook,
    renew_command,
    resolve_package,
    ubuntu_release,
)
from reemote.rolevars import resolve_vars
from tests.simulated_host import SimulatedHost

SCRIPT = "certbot"
CERT_NAME = "example.com"
DOMAINS = ("example.com", "www.example.com")
CERTS = [{"domains": list(DOMAINS)}]

# The exact shells the two act barriers render (the shell_results keys). sudo
# renders as a bare "sudo " prefix on the whole pipeline/command; the create
# carries the role's default standalone flags plus the nginx hook pair.
PROBE_SHELL = f"sudo {domains_command(SCRIPT, CERT_NAME)}"
CREATE_SHELL = f"sudo {create_command(SCRIPT, CertbotVars(), Cert(domains=DOMAINS), CERT_NAME)}"
CREATE_NOHOOKS = (
    "sudo certbot certonly --standalone --noninteractive --agree-tos "
    "--email email@example.com --cert-name example.com "
    "-d example.com,www.example.com"
)
WEBROOT_VARS = CertbotVars(create_method="webroot")
WEBROOT_CERT = Cert(domains=DOMAINS)
CREATE_WEBROOT = (
    f"sudo {create_command(SCRIPT, WEBROOT_VARS, WEBROOT_CERT, CERT_NAME)}"
)
OK = {"exit_status": 0, "stdout": "", "stderr": ""}
# The raw cut -d':' -f2 remainder of certbot's "  Domains: ..." line.
PROBE_MATCH = {"exit_status": 0, "stdout": f" {' '.join(DOMAINS)}", "stderr": ""}
PROBE_DRIFT = {"exit_status": 0, "stdout": " stale.example.com", "stderr": ""}


def _plant_cert(host: SimulatedHost, name: str) -> None:
    """Plant the ``live/<name>/cert.pem`` the create action just issued.

    A raw ``Shell`` action mutates no file store, so the real certbot's
    certificate would be absent when the *next* pass reads its gate. The
    symlink target (``archive/``) is not modelled -- ``FileExists`` is
    ``test -e`` on the live path, like the role's ``stat``.
    """
    path = f"{LIVE_DIR}/{name}/cert.pem"
    host.remote_files[path] = b"-----BEGIN CERTIFICATE-----\n"
    host.existing_files.add(path)
    host.file_stats[path] = {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": 27,
    }


class CertbotSimulatedHost(SimulatedHost):
    """``SimulatedHost`` with the create action's in-stream side effect.

    The *answer* still comes from ``shell_results`` through ``super()`` --
    nothing is faked; only the certificate the real certbot would leave
    behind is planted, the moment a ``certonly`` action succeeds.
    """

    async def _run_shell(self, shell: str) -> Any:
        result = await super()._run_shell(shell)
        if result.exit_status != 0 or " certonly " not in shell:
            return result
        name = shell.split("--cert-name ", 1)[1].split(" ", 1)[0]
        _plant_cert(self, name)
        return result


def seed(host: SimulatedHost) -> None:
    """Pre-state: a stock Ubuntu 22.04 machine, no certbot, no certs, no cron.

    The distribution seam answers the ``LinuxDistribution`` barrier (release
    ``22.04`` -> the ``certbot`` package); both action shells are seeded, so
    an unseeded new command would raise rather than silently pass.
    """
    host.linux_distribution = {
        "name": "Ubuntu",
        "family": "Debian",
        "major": 22,
        "minor": 4,
        "release_meta": {"VERSION_ID": "22.04", "VERSION_CODENAME": "jammy"},
    }
    host.shell_results = {PROBE_SHELL: dict(PROBE_MATCH), CREATE_SHELL: dict(OK)}
    host.ran_shells = []


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one RedHat host that must be skipped.

    The role variables ride in the entry's ``roles`` mapping under
    ``"certbot"``, bound by ``resolve_vars`` with bare field names.
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
        "roles": {"certbot": {}},
    }
    entry["roles"]["certbot"].update(overrides)
    return [
        entry,
        {
            "host": "web1",
            "family": "RedHat",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "centos",
        },
    ]


def make_hosts(inventory: list[dict[str, Any]]) -> dict[str, CertbotSimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, CertbotSimulatedHost] = {}
    for entry in inventory:
        host = CertbotSimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            seed(host)
        hosts[host.name] = host
    return hosts


async def run_role(
    inventory: list[dict[str, Any]],
    hosts: dict[str, CertbotSimulatedHost],
    *,
    dry: bool = False,
) -> Any:
    """Resolve variables, build the sync factory's deploy, and run one wave pass."""
    v = resolve_vars(CertbotVars, "certbot", inventory)

    def factory(entry: dict[str, Any]) -> CertbotSimulatedHost:
        # The *same* host object every pass, so state survives the barriers.
        return hosts[entry["host"]]

    # A plain (non-async) factory: called, not awaited.
    deploy = certbot(v)
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


# --- the first pass installs, creates the cert and schedules the renewal ------


async def test_first_pass_installs_creates_cert_and_cron():
    """The full stream: apt install, hook dirs/files, create action, renew cron."""
    inventory = make_inventory(create_if_missing=True, certs=CERTS)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # The package installed (the release table answered "certbot" for 22.04).
    assert DEFAULT_PACKAGE in host.apt_packages

    # The hook directories exist and both hook files landed at 0750 with the
    # f-string render of the two templates (D4).
    hook_content = render_service_hook("stop", ("nginx",)).encode()
    assert host.remote_files[PRE_HOOK_PATH] == hook_content
    assert host.file_stats[PRE_HOOK_PATH]["mode"] == 750
    assert host.remote_files[POST_HOOK_PATH] == render_service_hook(
        "start", ("nginx",)
    ).encode()
    assert host.file_stats[POST_HOOK_PATH]["mode"] == 750

    # The cert was absent, so only the create action ran -- no probe -- with
    # the exact rendered shell, and the planted cert is now visible.
    assert host.ran_shells == [CREATE_SHELL]
    assert f"{LIVE_DIR}/{CERT_NAME}/cert.pem" in host.existing_files

    # The renewal cron entry, whole-table edit through the crontab module.
    assert f"{SCRIPT} renew --quiet" in host.crontab_text
    assert f"# reemote-name={CRON_NAME}" in host.crontab_text
    assert "30 3 * * *" in host.crontab_text

    assert "Install Certbot." in entry_names(report)
    assert changed_entries(report)


async def test_second_pass_changes_nothing():
    """The idempotence claim: probe reads the (matching) domains, nothing changes."""
    inventory = make_inventory(create_if_missing=True, certs=CERTS)
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"
    shells_after_first = list(hosts["ubuntu"].ran_shells)

    second = await run_role(inventory, hosts)
    host = hosts["ubuntu"]
    assert changed_entries(second) == []
    # The cert exists now, so the probe runs (a changed_when: false read) and
    # its domains match: no second create action.
    assert host.ran_shells == shells_after_first + [PROBE_SHELL]


# --- the domain-drift branch ---------------------------------------------------


async def test_domains_changed_reruns_create():
    """An existing cert whose domains differ re-runs the create (upstream gate)."""
    inventory = make_inventory(create_if_missing=True, certs=CERTS)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    _plant_cert(host, CERT_NAME)
    host.shell_results[PROBE_SHELL] = dict(PROBE_DRIFT)

    await run_role(inventory, hosts)
    assert host.ran_shells == [PROBE_SHELL, CREATE_SHELL]


async def test_probe_failure_raises_command_error():
    """A failed probe is the role's own raise (an act barrier never raises)."""
    inventory = make_inventory(create_if_missing=True, certs=CERTS)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    _plant_cert(host, CERT_NAME)
    host.shell_results[PROBE_SHELL] = {
        "exit_status": 1,
        "stdout": "",
        "stderr": "no such certificate",
    }

    with pytest.raises(CommandError, match="certificates"):
        await run_role(inventory, hosts)


# --- the webroot method ---------------------------------------------------------


async def test_webroot_method_creates_directory_and_flags():
    inventory = make_inventory(
        create_if_missing=True, create_method="webroot", certs=CERTS
    )
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    webroot_shell = f"sudo {create_command(SCRIPT, WEBROOT_VARS, WEBROOT_CERT, CERT_NAME)}"
    host.shell_results[webroot_shell] = dict(OK)

    await run_role(inventory, hosts)

    # The webroot directory task ran; the standalone hook pair did not.
    assert "/var/www/letsencrypt" in host.directory_stats
    assert PRE_HOOK_PATH not in host.remote_files
    assert "--webroot-path /var/www/letsencrypt" in webroot_shell
    assert host.ran_shells == [webroot_shell]

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_empty_hook_services_skips_hooks_and_flags():
    """The role gates both the hook files and --pre/--post on a truthy list."""
    inventory = make_inventory(
        create_if_missing=True, create_standalone_stop_services=[], certs=CERTS
    )
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    host.shell_results[CREATE_NOHOOKS] = dict(OK)

    await run_role(inventory, hosts)

    assert PRE_HOOK_PATH not in host.remote_files
    assert POST_HOOK_PATH not in host.remote_files
    assert host.ran_shells == [CREATE_NOHOOKS]

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the renewal cron gates ------------------------------------------------------


async def test_create_if_missing_false_skips_the_cert_half():
    """The library default is the role's own gate: install + cron only."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    await run_role(inventory, hosts)

    assert host.ran_shells == []
    assert DEFAULT_PACKAGE in host.apt_packages
    assert f"{SCRIPT} renew --quiet" in host.crontab_text

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_auto_renew_false_skips_the_cron():
    inventory = make_inventory(auto_renew=False)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    await run_role(inventory, hosts)
    assert host.crontab_text == ""
    assert "Add cron job for certbot renewal." not in entry_names(
        await run_role(inventory, hosts)
    )


async def test_renew_user_root_targets_the_privileged_table():
    """user="root" maps to sudo=True (D6); the login user stays unprivileged."""
    inventory = make_inventory(auto_renew_user="root")
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)
    host = hosts["ubuntu"]
    assert f"{SCRIPT} renew --quiet" in host.crontab_text
    # The default (empty user) targets the connecting user's table instead.
    plain = make_inventory()
    plain_hosts = make_hosts(plain)
    await run_role(plain, plain_hosts)
    assert f"{SCRIPT} renew --quiet" in plain_hosts["ubuntu"].crontab_text


async def test_third_renew_user_raises():
    inventory = make_inventory(auto_renew_user="www-data")
    hosts = make_hosts(inventory)
    with pytest.raises(ValueError, match="third user"):
        await run_role(inventory, hosts)


# --- family guard and dry preview -------------------------------------------------


async def test_non_ubuntu_host_is_never_touched():
    inventory = make_inventory(create_if_missing=True, certs=CERTS)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    skipped = hosts["web1"]
    assert skipped.apt_packages == {}
    assert skipped.crontab_text == ""
    assert skipped.ran_shells == []
    # The family guard returns before the first yield, so the off-target host
    # emits no log entry at all.
    assert not any(entry.host == "web1" for entry in report.merged().log)


async def test_dry_preview_mutates_nothing():
    """``dry=True`` is one wave deep, so it parks at the distribution barrier."""
    inventory = make_inventory(create_if_missing=True, certs=CERTS)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    await run_role(inventory, hosts, dry=True)
    assert host.apt_packages == {}
    assert host.remote_files == {}
    assert host.ran_shells == []
    assert host.crontab_text == ""


# --- the pure helpers, unit-tested directly ----------------------------------------


def _host(**overrides: Any) -> CertbotSimulatedHost:
    entry = make_inventory(**overrides)[0]
    return CertbotSimulatedHost.from_inventory(entry)


def test_release_table_and_override():
    info = {"name": "Ubuntu", "release_meta": {"VERSION_ID": "16.04"}}
    assert resolve_package(CertbotVars(), info) == "letsencrypt"
    assert PACKAGE_BY_RELEASE["16.04"] == "letsencrypt"
    fresh = {"name": "Ubuntu", "release_meta": {"VERSION_ID": "24.04"}}
    assert resolve_package(CertbotVars(), fresh) == DEFAULT_PACKAGE
    pinned = CertbotVars(package="certbot")
    assert resolve_package(pinned, info) == "certbot"


def test_ubuntu_release_forms():
    assert ubuntu_release({"name": "Ubuntu", "release_meta": {"VERSION_ID": "20.04"}}) == "20.04"
    # No VERSION_ID: the major/minor pair formats the table key.
    assert ubuntu_release({"name": "Ubuntu", "major": 16, "minor": 4, "release_meta": {}}) == "16.04"
    with pytest.raises(ValueError, match="targets Ubuntu"):
        ubuntu_release({"name": "Debian", "release_meta": {"VERSION_ID": "12"}})
    with pytest.raises(ValueError, match="release"):
        ubuntu_release({"name": "Ubuntu", "release_meta": {}})


def test_cert_name_derivation():
    assert cert_name(Cert(domains=("*.example.com", "www.example.com"))) == "example.com"
    assert cert_name(Cert(name="custom", domains=("a.com",))) == "custom"


def test_create_command_flag_order_and_dedup():
    hv = CertbotVars(hsts=True, testmode=True, expand=True, create_extra_args="--rsa-key-size 4096")
    cert = Cert(domains=DOMAINS, deploy_hook="systemctl reload nginx")
    command = create_command("certbot", hv, cert, CERT_NAME)
    # --expand is emitted once (the upstream template emits it twice, D5).
    assert command.count(" --expand") == 1
    assert "--deploy-hook 'systemctl reload nginx'" in command
    assert "--pre-hook" in command and "--post-hook" in command
    assert "  " not in command


def test_create_command_webroot_and_email_precedence():
    hv = CertbotVars(create_method="webroot", webroot="/var/www/letsencrypt")
    cert = Cert(domains=DOMAINS, email="admin@x.io", webroot="/srv/le")
    command = create_command("certbot", hv, cert, CERT_NAME)
    assert "--email admin@x.io" in command
    assert "--webroot-path /srv/le" in command
    assert "--pre-hook" not in command


def test_parse_and_desired_domains_match():
    assert parse_domains(f" {' '.join(DOMAINS)} ") == desired_domains(Cert(domains=DOMAINS))
    assert parse_domains(" b.com a.com ") == ("a.com", "b.com")
    assert desired_domains(Cert(domains=(" a.com ", "a.com"))) == ("a.com", "a.com")


def test_renew_command_normalizes_whitespace():
    assert renew_command("certbot", "--quiet") == "certbot renew --quiet"
    assert renew_command("letsencrypt", "") == "letsencrypt renew"


def test_render_service_hook_text():
    assert render_service_hook("stop", ("nginx",)) == (
        "#!/bin/bash\n"
        "# Managed by reemote (migrated from geerlingguy.certbot)\n"
        "\n"
        'echo "stopping service nginx"\n'
        "systemctl stop nginx\n"
    )
    assert 'echo "starting service nginx"' in render_service_hook("start", ("nginx",))


def test_cron_privilege_mapping():
    host = _host()  # connection username: supervisor
    assert cron_privilege(CertbotVars(auto_renew_user=""), host) is False
    assert cron_privilege(CertbotVars(auto_renew_user="supervisor"), host) is False
    assert cron_privilege(CertbotVars(auto_renew_user="root"), host) is True
    with pytest.raises(ValueError, match="www-data"):
        cron_privilege(CertbotVars(auto_renew_user="www-data"), host)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"install_method": "snap"}, "scoped out"),
        ({"install_method": "source"}, "scoped out"),
        ({"create_method": "dns"}, "standalone"),
        ({"create_method": ""}, "standalone"),
        ({"certs": "example.com"}, "certs"),
        ({"create_standalone_stop_services": "nginx"}, "stop_services"),
    ],
)
def test_vars_reject_invalid_values(kwargs, match):
    # pydantic's ValidationError is a ValueError; so is every validator raise.
    with pytest.raises(ValueError, match=match):
        CertbotVars(**kwargs)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"domains": []}, "domain"),
        ({"domains": ["  "]}, "domain"),
        ({"domains": "example.com"}, "domain"),
        ({"domains": ["a.com"], "unknown": 1}, "unknown"),
    ],
)
def test_cert_rejects_invalid_values(kwargs, match):
    with pytest.raises(ValueError, match=match):
        Cert(**kwargs)


def test_cert_accepts_json_dict_shape():
    hv = CertbotVars(certs=[{"domains": ["a.com"], "deploy_hook": "x"}])
    assert hv.certs == (Cert(domains=("a.com",), deploy_hook="x"),)
