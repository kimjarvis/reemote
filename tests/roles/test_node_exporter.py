# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``node_exporter`` role (§17.5).

The role is library code now, so it is imported (``from reemote.roles import
node_exporter``) and replayed against ``SimulatedHost`` through the real wave
driver: ``run_waves(inventory, node_exporter(v), host_factory=...)``. The
factory is plain (non-``async``): the control-side fetches -- the GitHub
releases lookup behind ``version: "latest"`` and the tarball fetch inside
``files.unarchive`` -- ride the same ``files._fetch_url_bytes`` seam the
zabbix/datadog tests stub, so the whole stream answers offline.

Two simulation seams are bridged explicitly, both through the zabbix
``settle`` precedent of re-deriving an answer from the simulated filesystem
before every pass:

* the ``--version`` probe (the role's ``act(Shell)`` gate) is (re)seeded from
  whether the binary is installed, so the *first* pass sees the absent-binary
  answer (exit 127, empty stdout -- the ``failed_when: false`` reading) and a
  *second* pass sees the installed version and skips the download half;
* the tarball's member listing rides ``archive_members``, keyed by the
  URL-derived ``/tmp`` spool path, because the simulation parses no real
  archive (extraction is exactly what ``files.unarchive`` does with these
  members).

The load-bearing assertion is the migration's idempotence claim: after a
first pass that installs the binary, the accounts, the unit and the running
service, the second pass reports no change anywhere -- the probe gate skips
the download pair entirely (``files.copy``'s sha1 comparison cannot converge
on extracted member files, which carry no content bytes in the simulation, so
it is the ``act`` probe, not the operation, that carries convergence here),
every configure request reads back its own state, no notification fires, and
the handler wave simply does not exist.

The metrics gate (deviation D8) is the ``http_endpoints`` seam: seeded to a
200 landing page naming the ``Metrics`` link the deploy passes, unseeded (or
lacking the marker) it fails the body with the role's ``failed_when`` error.
"""

import hashlib
import json
from typing import Any

import pytest

from reemote import run_waves
from reemote.modules.files import files as files_module
from reemote.roles import NodeExporterVars, node_exporter
from reemote.roles.node_exporter import (
    DOWNLOAD_BASE,
    MOVE_NAME,
    RELEASE_API_URL,
    UNIT_NAME,
    UNIT_PATH,
    download_url_for,
    metrics_url,
    parse_release_tag,
    render_unit,
)
from reemote.rolevars import resolve_vars
from tests.simulated_host import SimulatedHost

VERSION = "1.8.2"
ARCH = "amd64"
BIN_PATH = "/usr/local/bin/node_exporter"
TARBALL_URL = download_url_for(VERSION, ARCH)
EXTRACT_DIR_PATH = f"/tmp/node_exporter-{VERSION}.linux-{ARCH}"
FAKE_BINARY = b"\x7fELF\x02\x01\x00 node_exporter-canned-bytes \x00"
API_BODY = json.dumps({"tag_name": f"v{VERSION}"}).encode()
METRICS_URL = "http://localhost:9100/"
METRICS_BODY = (
    "<html><head><title>Node Exporter</title></head>"
    "<body><h1>Node Exporter</h1>"
    "<p><a href=\"/metrics\">Metrics</a></p></body></html>"
)

#: The exact action shell the ``act(Shell)`` probe renders (no ``sudo`` --
#: the version probe runs as the login user, as upstream).
PROBE_SHELL = f"{BIN_PATH} --version"

#: The request names, verbatim from the role module (the notification channel).
UNARCHIVE_NAME = "Download and unarchive node_exporter into temporary location."
GROUP_NAME = "Create node_exporter group (optional fixed GID)"
USER_NAME = "Create node_exporter user (optional fixed UID)"
SERVICE_NAME = "Ensure node_exporter is running and enabled at boot."
HANDLER_NAME = "Restart node_exporter (handler)"


def spool_for(url: str) -> str:
    """The URL-derived ``/tmp`` spool ``files.unarchive`` stages the tarball to."""
    name = url.rsplit("/", 1)[-1]
    digest = hashlib.sha1(url.encode()).hexdigest()[:16]
    return f"/tmp/reemote-unarchive-{digest}-{name}"


@pytest.fixture
def canned_fetch(monkeypatch):
    """Stub every control-side fetch the body performs.

    ``files.fetch_url`` and the URL branch of ``files.unarchive`` both look
    ``_fetch_url_bytes`` up as a module global at call time. The releases API
    answers with the pinned tag; any other URL is the tarball's canned bytes.
    The returned list records which URLs were fetched, in order.
    """
    fetched: list[str] = []

    def fake_fetch(url: str) -> bytes:
        fetched.append(url)
        if url == RELEASE_API_URL:
            return API_BODY
        return FAKE_BINARY

    monkeypatch.setattr(files_module, "_fetch_url_bytes", fake_fetch)
    return fetched


def refresh_probe(host: SimulatedHost, version: str = VERSION) -> None:
    """Re-derive the ``--version`` probe answer from the simulated filesystem.

    The bridge the zabbix ``settle`` performs for ``find``: while the binary
    is absent the probe answers the real missing-command record (exit 127,
    empty stdout -- data, not an error, per the role's ``failed_when:
    false``); once installed it answers with the version banner, which is
    what makes the second pass skip the download half.
    """
    if BIN_PATH in host.existing_files:
        host.shell_results[PROBE_SHELL] = {
            "exit_status": 0,
            "stdout": f"node_exporter, version {version} (branch: HEAD)\n",
            "stderr": "",
        }
    else:
        host.shell_results[PROBE_SHELL] = {
            "exit_status": 127,
            "stdout": "",
            "stderr": "command not found",
        }


def seed(host: SimulatedHost, tarball_url: str = TARBALL_URL) -> None:
    """Pre-state: a stock Ubuntu machine with neither binary nor accounts.

    ``/tmp`` (the extraction target, mode 1777 as on the real machine) and
    ``/usr/local/bin`` (the copy's rename parent, which must pre-exist) are
    the only directories; the tarball member listing is keyed by the spool
    path the URL derives; the metrics endpoint answers the landing page.
    """
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}
    host.directory_stats = {
        "/tmp": {"mode": 1777, "user": "root", "group": "root"},
        "/usr/local/bin": {"mode": 755, "user": "root", "group": "root"},
    }
    host.existing_files |= set(host.directory_stats)
    host.archive_members = {
        spool_for(tarball_url): (
            f"node_exporter-{VERSION}.linux-{ARCH}/",
            f"node_exporter-{VERSION}.linux-{ARCH}/node_exporter",
        ),
    }
    host.http_endpoints = {METRICS_URL: {"status": 200, "body": METRICS_BODY}}
    refresh_probe(host)


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride the entry's ``roles`` mapping under
    ``"node_exporter"``, written with the model's bare field names; the
    default pins the version so a plain run resolves no ``latest``.
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
            "node_exporter": {
                "version": VERSION,
            },
        },
    }
    entry["roles"]["node_exporter"].update(overrides)
    return [
        entry,
        {
            "host": "web1",
            "family": "RedHat",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "centos",
        },
    ]


def make_hosts(
    inventory: list[dict[str, Any]], **overrides: Any
) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            url = overrides.get("download_url") or TARBALL_URL
            seed(host, tarball_url=url)
        hosts[host.name] = host
    return hosts


async def run_role(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SimulatedHost],
    *,
    dry: bool = False,
) -> Any:
    """Resolve variables, re-bridge the probe seam, and run one wave pass."""
    v = resolve_vars(NodeExporterVars, "node_exporter", inventory)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        # The *same* host object every pass, so state survives the barriers.
        return hosts[entry["host"]]

    for host in hosts.values():
        refresh_probe(host)

    deploy = node_exporter(v)  # a plain factory: called, not awaited
    report, _hosts = await run_waves(
        inventory, deploy, host_factory=factory, dry=dry
    )
    return report


def changed_entries(report: Any) -> list[Any]:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def entry_names(report: Any, host: str = "ubuntu") -> set[str]:
    """Every log entry name for one host."""
    return {entry.name for entry in report.merged().log if entry.host == host}


# --- the first pass really deploys the role -------------------------------------


async def test_first_pass_installs_and_runs(canned_fetch):
    """The full stream: probe, download pair, accounts, unit, service, handler."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # The pinned version fetched only the tarball -- no releases API call.
    assert canned_fetch == [TARBALL_URL]

    # The tarball was spooled, extracted and cleaned; the members landed.
    assert spool_for(TARBALL_URL) not in host.remote_files
    assert EXTRACT_DIR_PATH in host.directory_stats
    assert EXTRACT_DIR_PATH in host.existing_files
    assert f"{EXTRACT_DIR_PATH}/node_exporter" in host.existing_files

    # The binary sits at the configured path, executable (deviation D4: the
    # bit comes from the copy, not a recursive chmod of /tmp).
    assert BIN_PATH in host.existing_files
    assert host.file_stats[BIN_PATH]["mode"] == 755

    # The account exists, shell-locked and homeless (deviation D10); no group
    # request was made, so useradd's own group carries the name (D11).
    assert host.os_users["node_exporter"]["shell"] == "/sbin/nologin"
    assert host.os_users["node_exporter"]["group"] == "node_exporter"
    assert "node_exporter" not in host.os_groups

    # The rendered unit landed at 0644 (deviation D12).
    unit = host.remote_files[UNIT_PATH].decode()
    assert f"ExecStart={BIN_PATH} --web.listen-address=:9100\n" in unit
    assert "Restart=on-failure\n" in unit
    assert host.file_stats[UNIT_PATH]["mode"] == 644

    # The service runs and boots; the handler restarted it after the changes.
    assert host.services["node_exporter.service"] == "active"
    assert "node_exporter.service" in host.enabled_services
    assert not host.systemd_reload_pending  # the request's reload cleared it

    assert [entry.name for entry in changed_entries(report)] == [
        UNARCHIVE_NAME,
        MOVE_NAME,
        USER_NAME,
        UNIT_NAME,
        SERVICE_NAME,
        HANDLER_NAME,
    ]


async def test_non_ubuntu_host_is_never_touched(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    skipped = hosts["web1"]
    assert skipped.remote_files == {}
    assert skipped.existing_files == set()
    assert skipped.os_users == {}
    # The family guard returns before the first yield: not even the probe ran.
    assert skipped.ran_shells == []
    assert not any(entry.host == "web1" for entry in report.merged().log)


# --- the second pass is a no-op --------------------------------------------------


async def test_second_pass_changes_nothing(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    # The probe gate skips the download half *entirely*: nothing is fetched,
    # not even the creates-gated unarchive.
    assert canned_fetch == [TARBALL_URL]
    # The converged stream still plans the four configure requests (unchanged)
    # and nothing else -- no handler wave exists at all.
    assert entry_names(second) == {USER_NAME, UNIT_NAME, SERVICE_NAME}
    assert HANDLER_NAME not in entry_names(second)


# --- ``latest`` and the control barrier (deviations D2, D3) -----------------------


async def test_latest_resolves_through_the_releases_api(canned_fetch):
    """The default version costs a control fetch and one extra wave position."""
    inventory = make_inventory(version="latest")
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    # API first, then the tarball whose URL the resolved tag built.
    assert canned_fetch == [RELEASE_API_URL, TARBALL_URL]
    assert EXTRACT_DIR_PATH in hosts["ubuntu"].directory_stats
    assert BIN_PATH in hosts["ubuntu"].existing_files

    second = await run_role(inventory, hosts)
    # The converged pass still asks the API who is latest (the version is a
    # local name, never remembered state -- D3), but the probe gate then
    # skips the download: no second tarball fetch.
    assert canned_fetch == [RELEASE_API_URL, TARBALL_URL, RELEASE_API_URL]
    assert changed_entries(second) == []


async def test_download_url_override_replaces_the_formula(canned_fetch):
    """The mirrored-archives knob (deviation D5's configurable side)."""
    mirrored = "https://mirror.example.com/node_exporter/node_exporter.tar.gz"
    inventory = make_inventory(download_url=mirrored)
    hosts = make_hosts(inventory, download_url=mirrored)
    await run_role(inventory, hosts)

    assert canned_fetch == [mirrored]
    assert BIN_PATH in hosts["ubuntu"].existing_files


async def test_preinstalled_version_skips_the_download_half(canned_fetch):
    """The probe's ``version in stdout`` reading: a matching host downloads nothing."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    # Present *and* current before the role ever runs.
    host.existing_files.add(BIN_PATH)
    host.file_stats[BIN_PATH] = {"mode": 755, "user": "root", "group": "root"}

    report = await run_role(inventory, hosts)
    assert canned_fetch == []
    assert UNARCHIVE_NAME not in entry_names(report)
    assert MOVE_NAME not in entry_names(report)
    # The configure half still runs (the unit is new), so the handler fired.
    assert UNIT_NAME in {entry.name for entry in changed_entries(report)}


# --- the metrics gate (deviations D8, D9) ------------------------------------------


async def test_metrics_without_the_page_fails_the_deploy(canned_fetch):
    """The ``failed_when`` of the ``uri`` task, reproduced as a body error."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    hosts["ubuntu"].http_endpoints = {}  # an exporter that answers nothing

    with pytest.raises(ValueError, match="not serving metrics"):
        await run_role(inventory, hosts)
    # The error is raised after every wave was applied: the deploy itself ran.
    assert hosts["ubuntu"].services["node_exporter.service"] == "active"


async def test_metrics_page_without_the_marker_fails(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    hosts["ubuntu"].http_endpoints = {
        METRICS_URL: {"status": 200, "body": "<html>wrong proxy</html>"}
    }
    with pytest.raises(ValueError, match="not serving metrics"):
        await run_role(inventory, hosts)


async def test_custom_listen_address_checks_that_url(canned_fetch):
    """``node_exporter_host``/``port``: the unit line and the check URL agree."""
    inventory = make_inventory(host="10.0.0.5", port=9101)
    hosts = make_hosts(inventory)
    hosts["ubuntu"].http_endpoints = {
        "http://10.0.0.5:9101/": {"status": 200, "body": METRICS_BODY}
    }
    await run_role(inventory, hosts)

    unit = hosts["ubuntu"].remote_files[UNIT_PATH].decode()
    assert "ExecStart=/usr/local/bin/node_exporter --web.listen-address=10.0.0.5:9101\n" in unit
    # The default localhost URL was never consulted.
    assert METRICS_URL not in hosts["ubuntu"].http_endpoints


# --- the optional ids and states -----------------------------------------------------


async def test_uid_gid_pins_create_the_group(canned_fetch):
    inventory = make_inventory(uid=940, gid=940)
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    assert "node_exporter" in host.os_groups
    assert host.os_group_details["node_exporter"]["gid"] == 940
    assert host.os_users["node_exporter"]["uid"] == 940
    assert host.os_users["node_exporter"]["group"] == "node_exporter"
    # The group request joins the changed list between the copy and the user.
    assert [entry.name for entry in changed_entries(report)] == [
        UNARCHIVE_NAME,
        MOVE_NAME,
        GROUP_NAME,
        USER_NAME,
        UNIT_NAME,
        SERVICE_NAME,
        HANDLER_NAME,
    ]

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_state_stopped_leaves_the_unit_inactive(canned_fetch):
    inventory = make_inventory(state="stopped")
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # The configure request never starts the unit -- but the restart handler
    # the new binary and unit queue *does*, exactly as Ansible's end-of-play
    # handler flush after a ``state: stopped`` task.
    assert host.services["node_exporter.service"] == "active"
    # Even a stopped unit is enabled at boot by the role's default.
    assert "node_exporter.service" in host.enabled_services
    assert HANDLER_NAME in {entry.name for entry in changed_entries(first)}

    # The next pass notifies nobody (everything converged), so the wanted
    # stopped state finally wins -- the service request reports the drift.
    second = await run_role(inventory, hosts)
    assert [entry.name for entry in changed_entries(second)] == [SERVICE_NAME]
    assert host.services["node_exporter.service"] == "inactive"

    third = await run_role(inventory, hosts)
    assert changed_entries(third) == []


# --- the handler re-fires exactly on a config change ---------------------------------


async def test_unit_change_notifies_the_handler_again(canned_fetch):
    """The checkpoint's notification read, exercised across two configurations.

    A second pass with a changed ``options`` skips the download half (the
    probe still matches) but rewrites the unit -- and the rewrite is one of
    the two notifying tasks, so the restart handler runs again.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    inventory[0]["roles"]["node_exporter"]["options"] = (
        "--collector.textfile.directory=/var/lib/node_exporter"
    )
    second = await run_role(inventory, hosts)
    names = {entry.name for entry in changed_entries(second)}
    # The rewrite notifies; the same wave's service request also carries the
    # state-gated ``daemon-reload`` the fresh unit file made pending (D7).
    assert names == {UNIT_NAME, SERVICE_NAME, HANDLER_NAME}
    assert canned_fetch == [TARBALL_URL], "the drifted unit never refetches"
    assert (
        "--collector.textfile.directory=/var/lib/node_exporter"
        in hosts["ubuntu"].remote_files[UNIT_PATH].decode()
    )


# --- the dry preview ----------------------------------------------------------------


async def test_dry_preview_mutates_nothing(canned_fetch):
    """``dry=True`` stops at the probe barrier: no fetch, no write."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    before_dirs = dict(host.directory_stats)

    await run_role(inventory, hosts, dry=True)
    assert canned_fetch == []
    assert host.remote_files == {}
    assert host.file_stats == {}
    assert host.directory_stats == before_dirs
    assert host.os_users == {}
    assert host.services == {}


# --- the pure helpers, unit-tested directly -------------------------------------------


def test_parse_release_tag_strips_the_leading_v():
    assert parse_release_tag('{"tag_name": "v1.8.2"}') == "1.8.2"
    assert parse_release_tag('{"tag_name": "1.8.2"}') == "1.8.2"


def test_parse_release_tag_keeps_unmatched_tags_verbatim():
    # ``regex_replace`` leaves a non-``^[0-9.]+$`` tag untouched; so do we.
    assert parse_release_tag('{"tag_name": "2.0.0-rc.1"}') == "2.0.0-rc.1"


@pytest.mark.parametrize(
    "text",
    ["not json at all", "[]", '{"name": "node_exporter"}', '{"tag_name": ""}'],
)
def test_parse_release_tag_rejects_unusable_bodies(text):
    with pytest.raises(ValueError):
        parse_release_tag(text)


def test_download_url_formula_matches_defaults_main():
    assert TARBALL_URL == (
        "https://github.com/prometheus/node_exporter/releases/download"
        "/v1.8.2/node_exporter-1.8.2.linux-amd64.tar.gz"
    )
    assert TARBALL_URL == (
        f"{DOWNLOAD_BASE}/v{VERSION}/node_exporter-{VERSION}.linux-{ARCH}.tar.gz"
    )


def test_metrics_url_substitutes_localhost():
    assert metrics_url(NodeExporterVars(host="")) == "http://localhost:9100/"
    assert metrics_url(
        NodeExporterVars(host="192.168.1.5", port=9101)
    ) == "http://192.168.1.5:9101/"


def test_render_unit_reproduces_the_template():
    unit = render_unit(
        NodeExporterVars(
            options="--no-collector.hwmon", restart="always", port=9102
        )
    )
    assert unit.splitlines() == [
        "[Unit]",
        "Description=NodeExporter",
        "",
        "[Service]",
        "TimeoutStartSec=0",
        "User=node_exporter",
        f"ExecStart={BIN_PATH} --web.listen-address=:9102 --no-collector.hwmon",
        "Restart=always",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
    ]
    # D12: an empty options string leaves no trailing space behind.
    assert "ExecStart=... \n" not in unit
    plain = render_unit(NodeExporterVars())
    assert f"ExecStart={BIN_PATH} --web.listen-address=:9100\n" in plain
    assert f"ExecStart={BIN_PATH} --web.listen-address=:9100 \n" not in plain


# --- variable validation ----------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"version": ""}, "node_exporter_version"),
        ({"version": "v1/../x"}, "plain release name"),
        ({"arch": ""}, "node_exporter_arch"),
        ({"arch": "amd 64"}, "plain architecture name"),
        ({"download_url": "ftp://m/x.tar.gz"}, "node_exporter_download_url"),
        ({"bin_path": "bin/node_exporter"}, "absolute path"),
        ({"host": "10.0.0.1:9100"}, "bare address"),
        ({"port": 0}, "between 1 and 65535"),
        ({"port": 70000}, "between 1 and 65535"),
        ({"state": "enabled"}, "node_exporter_state"),
        ({"restart": "sometimes"}, "node_exporter_restart"),
        ({"uid": 0}, "positive int"),
        ({"gid": -5}, "positive int"),
        ({"unknown_knob": True}, "unknown_knob"),
    ],
)
def test_vars_reject_invalid_values(kwargs, match):
    # pydantic's ValidationError is a ValueError; so is every validator raise.
    with pytest.raises(ValueError, match=match):
        NodeExporterVars(**kwargs)


def test_vars_accept_the_defaults_main_shape():
    hv = NodeExporterVars()
    assert hv.version == "latest"
    assert hv.arch == "amd64"
    assert hv.bin_path == BIN_PATH
    assert hv.port == 9100
    assert hv.state == "started"
    assert hv.enabled is True
    assert hv.restart == "on-failure"
    assert hv.uid is None
    assert hv.gid is None
