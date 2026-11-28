# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``zabbix_agent`` role (§17.5).

The role is library code now, so it is imported (``from reemote.roles import
zabbix_agent``) and replayed against ``SimulatedHost`` through the real wave
driver: ``run_waves(inventory, await zabbix_agent(v), host_factory=...)``. The
factory is ``async def`` -- it fetches the vendor signing key control-side
before returning the deploy -- so the test stubs ``files._fetch_url_bytes``
(the datadog precedent) and awaits the factory.

The load-bearing assertion is unchanged: run the role, then run it *again*
against the same persistent hosts, and the second pass reports no change
anywhere and queues no restart handler. The converged tree has one wave fewer
-- the handler wave simply does not exist -- which is the checkpoint's
notification read proving itself.

Two simulation seams are bridged explicitly between the passes:

* ``install_apt_key`` records a stub ``AptKey`` whose fingerprint is derived
  from the content hash, so the realistic ``...A14FE591`` fingerprint the real
  keyring would carry is seeded by hand in ``settle`` (issue #53's shape; the
  same bridge datadog documents);
* the ``find`` answer is recomputed from the simulated filesystem, so the
  second pass observes the first pass's purge and repository write.
"""

import fnmatch
from typing import Any

import pytest

from reemote import run_waves
from reemote.modules.files import FindFiles
from reemote.modules.files import files as files_module
from reemote.roles import ZabbixAgentVars, zabbix_agent
from reemote.roles.zabbix_agent import (
    APT_PREFERENCES_DIR,
    CONF_PATH,
    GETENFORCE_PROBE,
    POLICYCOREUTILS_PACKAGE,
    REPO_KEYRING,
    SELINUX_DOMAIN,
    SOURCES_DIR,
    SOURCES_PATTERN,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from tests.simulated_host import SimulatedHost

#: The armored key body the async factory fetches (no OpenPGP parsing in the
#: simulation, so the text only needs the armor marker ``apt.key`` sniffs).
KEY_TEXT = (
    "-----BEGIN PGP PUBLIC KEY BLOCK-----\n\n"
    "simulated zabbix key body, not real key material\n"
    "-----END PGP PUBLIC KEY BLOCK-----\n"
)

#: The pinned 8-hex short id the frozen ``sign_keys`` table gives focal.
SIGN_KEY_SHORT = "A14FE591"

#: The stale 4.2 source the seeded ``find`` returns, and the managed 4.4 file.
STALE_LIST = f"{SOURCES_DIR}/repo_zabbix_com_zabbix_4_2_ubuntu.list"
MANAGED_LIST = f"{SOURCES_DIR}/repo_zabbix_com_zabbix_4_4_ubuntu.list"
DEB_LINE = "deb http://repo.zabbix.com/zabbix/4.4/ubuntu focal main"
DEB_SRC_LINE = "deb-src http://repo.zabbix.com/zabbix/4.4/ubuntu focal main"

#: The action-shell key ``SimulatedHost.shell_results`` wants (issue #16):
#: the ``sudo``-rendered probe, verbatim.
GETENFORCE_SHELL = f"sudo {GETENFORCE_PROBE}"

#: The request names of the install wave, in body order.
INSTALL_NAMES = (
    "Debian | Install gpg key",
    "Debian | Installing deb repository Ubuntu",
    "Debian | Installing deb-src repository Ubuntu",
    "Debian | Installing zabbix-agent",
    "Debian | Enable the service",
)
#: The request names of the configure wave (default inventory: no PSK, no
#: SELinux), plus the checkpoint-selected handler.
CONFIGURE_NAMES = (
    "Configure zabbix-agent",
    "Create include dir zabbix-agent",
    "Make sure the zabbix-agent service is running",
)
HANDLER_NAME = "Restart zabbix-agent (handler)"

REMOVE_NAME = f"Debian | Remove unecessary zabbix repository {STALE_LIST}"


@pytest.fixture
def canned_fetch(monkeypatch):
    """Stub the control-side key fetch the async factory performs.

    ``files.fetch_url`` looks ``_fetch_url_bytes`` up as a module global at
    call time, so patching it redirects the factory's await. The returned list
    records which URLs were fetched -- the ``repo != "zabbix"`` gate reads it.
    """
    fetched: list[str] = []

    def fake_fetch(url: str) -> bytes:
        fetched.append(url)
        return KEY_TEXT.encode("utf-8")

    monkeypatch.setattr(files_module, "_fetch_url_bytes", fake_fetch)
    return fetched


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host to be skipped.

    The role variables ride the entry's ``roles`` mapping under
    ``"zabbix_agent"``, written with the model's bare field names, so a test
    changes a variable exactly the way an operator does.
    """
    entry: dict[str, Any] = {
        "host": "ubuntu",
        "family": "Ubuntu",
        "connection": {
            "address": "192.0.2.10",
            "username": "kjarvis",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "ubuntu",
        "roles": {
            "zabbix_agent": {
                "server": "10.0.0.1",
                "serveractive": "10.0.0.1",
            },
        },
    }
    entry["roles"]["zabbix_agent"].update(overrides)
    return [
        entry,
        {
            "host": "web1",
            "family": "RedHat",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "centos",
        },
    ]


def refresh_search_seams(host: SimulatedHost) -> None:
    """Re-derive the ``find`` answer from the simulated filesystem."""
    host.find_results = {
        FindFiles.command(SOURCES_DIR, fname=SOURCES_PATTERN): sorted(
            path
            for path in host.remote_files
            if path.startswith(f"{SOURCES_DIR}/")
            and fnmatch.fnmatch(path, f"{SOURCES_DIR}/{SOURCES_PATTERN}")
        ),
    }


def seed(host: SimulatedHost) -> None:
    """Pre-state: a stock Ubuntu 20.04 with a stale 4.2 source and no agent.

    The getenforce probe is seeded as the *absent-binary* answer (exit 1): on
    Ubuntu SELinux is not enforcing, and a non-zero exit is data -- exactly the
    D6 reading.
    """
    host.lsb_release = {
        "id": "Ubuntu",
        "description": "Ubuntu 20.04.6 LTS",
        "release": "20.04",
        "codename": "focal",
    }
    host.apt_packages = {}
    host.remote_files = {STALE_LIST: b"deb http://repo.zabbix.com/zabbix/4.2/ubuntu focal main\n"}
    host.existing_files = set(host.remote_files)
    host.file_stats = {
        path: {"user": "root", "group": "root", "mode": 644, "size": len(data)}
        for path, data in host.remote_files.items()
    }
    host.shell_results = {
        GETENFORCE_SHELL: {"exit_status": 1, "stdout": "", "stderr": ""},
    }
    refresh_search_seams(host)


def settle(host: SimulatedHost) -> None:
    """Bridge the subsystems the simulation does not model, between passes."""
    # ``install_apt_key`` stored a content-hash stub fingerprint; the real
    # keyring holds the vendor key whose long id ends with the short id the
    # role pins (D4's table row for focal).
    for entry in host.apt_keys:
        entry["fingerprint"] = f"A183B5AC44C7EDCE1D4C46E6D9C99AEF{SIGN_KEY_SHORT}"
        entry["id"] = f"D9C99AEF{SIGN_KEY_SHORT}"
    refresh_search_seams(host)


@pytest.fixture
def rig() -> tuple[list[dict[str, Any]], dict[str, SimulatedHost]]:
    """An inventory plus the persistent simulated hosts it materializes into."""
    inventory = make_inventory()
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            seed(host)
        hosts[host.name] = host
    return inventory, hosts


async def run_role(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SimulatedHost],
    *,
    dry: bool = False,
) -> Any:
    """Resolve variables, await the async factory, and run one wave pass."""
    v = resolve_vars(ZabbixAgentVars, "zabbix_agent", inventory)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    deploy = await zabbix_agent(v)
    report, _hosts = await run_waves(
        inventory, deploy, host_factory=factory, dry=dry
    )
    return report


def changed_entries(report: Any) -> list[Any]:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def entry_names(report: Any, host: str = "ubuntu") -> list[str]:
    """Every log entry name for one host, in order."""
    return [e.name for e in report.merged().log if e.host == host]


def request_attr(report: Any, name: str, attr: str) -> Any:
    """One meta attribute of the one planned request called *name*."""
    record = next(
        record
        for record in report.records
        if record.planned is not None and record.planned.request.name == name
    )
    return getattr(record.planned.request, attr)


# --- the first pass really deploys the role -----------------------------------


async def test_first_pass_installs_and_configures_the_agent(rig, canned_fetch):
    inventory, hosts = rig
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # The async factory fetched the one signing key control-side.
    assert canned_fetch == ["http://repo.zabbix.com/zabbix-official-repo.key"]

    # The keyring landed at the frozen global-trust path (D3), mode 0644.
    assert host.remote_files[REPO_KEYRING] == KEY_TEXT.encode()
    assert host.file_stats[REPO_KEYRING]["mode"] == 644

    # The stale source is purged, the managed file carries both lines (D8).
    assert STALE_LIST not in host.existing_files
    repo_text = host.remote_files[MANAGED_LIST].decode()
    assert repo_text.splitlines() == [DEB_LINE, DEB_SRC_LINE]

    # The three agent packages are installed, without recommends.
    for package in ("zabbix-agent", "zabbix-sender", "zabbix-get"):
        assert package in host.apt_packages

    # The rendered conf, with the role's ownership and mode (D12/D13).
    conf = host.remote_files[CONF_PATH].decode()
    assert conf.startswith("# Ansible managed\n")
    assert "Server=10.0.0.1" in conf
    assert "ServerActive=10.0.0.1" in conf
    assert "Hostname=ubuntu" in conf
    assert "ListenPort=10050" in conf
    assert host.file_stats[CONF_PATH] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(conf.encode()),
    }

    # The service ends enabled and running; the handler restarted it.
    assert host.services["zabbix-agent.service"] == "active"
    assert "zabbix-agent.service" in host.enabled_services

    assert [e.name for e in changed_entries(report)] == [
        INSTALL_NAMES[0],
        REMOVE_NAME,
        *INSTALL_NAMES[1:],
        *CONFIGURE_NAMES,
        HANDLER_NAME,
    ]


async def test_barriers_carry_the_interleaved_reads(rig, canned_fetch):
    """LsbRelease, FindFiles, the probe and the checkpoint all sit in-stream.

    The role's four reads -- the release fact, the repository ``find``, the
    getenforce ``command`` and the handler notification -- are the reason the
    play needed registered variables between its task files; here they are
    wave boundaries, and the install wave follows the purge sweep's answer.
    """
    inventory, hosts = rig
    report = await run_role(inventory, hosts)

    records = [record for record in report.records if record.host == "ubuntu"]
    barrier_kinds = [
        type(record.barrier).__name__ for record in records if record.barrier
    ]
    assert barrier_kinds == [
        "FactRequest",
        "FactRequest",
        "ActionRequest",
        "CheckpointRequest",
    ]
    assert sorted({record.wave for record in records}) == [0, 1, 2, 3, 4]

    # The getenforce probe's Result is data -- exit 1, no SELinux (D6).
    probe = next(
        record
        for record in records
        if type(record.barrier).__name__ == "ActionRequest"
    )
    assert probe.outcome["exit_status"] == 1

    # The install requests ride the wave after the FindFiles barrier, and the
    # removal names the stale path that barrier returned.
    removal_wave = next(
        record.wave
        for record in records
        if record.planned is not None and record.planned.request.name == REMOVE_NAME
    )
    assert removal_wave == 2


async def test_repo_purge_keeps_the_managed_file(rig, canned_fetch):
    """The find's ``excludes`` counterpart: the managed file is never removed.

    A host that already carries the managed repository file (both seams: the
    parsed source entry and the file itself) keeps it -- the ``deb`` request is
    a no-op there, and only the stale 4.2 file is purged.
    """
    inventory, hosts = rig
    host = hosts["ubuntu"]
    host.apt_sources = [
        {
            "type": "deb",
            "url": "http://repo.zabbix.com/zabbix/4.4/ubuntu",
            "distribution": "focal",
            "components": ["main"],
            "options": {},
            "filename": MANAGED_LIST,
        }
    ]
    host.remote_files[MANAGED_LIST] = f"{DEB_LINE}\n".encode()
    host.existing_files.add(MANAGED_LIST)
    host.file_stats[MANAGED_LIST] = {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(DEB_LINE) + 1,
    }
    refresh_search_seams(host)

    report = await run_role(inventory, hosts)

    assert STALE_LIST not in host.existing_files
    assert host.remote_files[MANAGED_LIST].decode().splitlines() == [
        DEB_LINE,
        DEB_SRC_LINE,
    ]
    changed = [e.name for e in changed_entries(report)]
    assert "Debian | Installing deb repository Ubuntu" not in changed
    assert "Debian | Installing deb-src repository Ubuntu" in changed


async def test_non_ubuntu_host_emits_nothing(rig, canned_fetch):
    """The family guard is the body's opening ``if`` (D1)."""
    inventory, hosts = rig
    report = await run_role(inventory, hosts)

    skipped = hosts["web1"]
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}
    log = report.merged().log
    assert not [e for e in log if e.host == "web1"]


async def test_src_reinstall_false_skips_the_purge_sweep(rig, canned_fetch):
    """D8's compound gate: ``src_reinstall`` skips the ``find`` barrier itself.

    The role's ``find`` task is gated on both ``zabbix_repo`` and
    ``zabbix_agent_src_reinstall``, so the whole wave vanishes offline — the
    keyring and the repository lines still install, the stale source stays.
    """
    inventory, hosts = rig
    inventory[0]["roles"]["zabbix_agent"]["src_reinstall"] = False
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    assert "FindFiles" not in report.tree()
    assert STALE_LIST in host.remote_files
    assert REMOVE_NAME not in entry_names(report)
    # The vendor block is untouched: keyring and both repository lines.
    assert REPO_KEYRING in host.remote_files
    assert host_repo_text(host).splitlines() == [DEB_LINE, DEB_SRC_LINE]

    records = [record for record in report.records if record.host == "ubuntu"]
    barrier_kinds = [
        type(record.barrier).__name__ for record in records if record.barrier
    ]
    assert barrier_kinds == ["FactRequest", "ActionRequest", "CheckpointRequest"]
    # One wave fewer than the default stream: no purge read, no purge wave.
    assert sorted({record.wave for record in records}) == [0, 1, 2, 3]


# --- the second pass is a no-op ------------------------------------------------


async def test_second_pass_changes_nothing_and_queues_no_handler(rig, canned_fetch):
    inventory, hosts = rig
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    for host in hosts.values():
        if host.name == "ubuntu":
            settle(host)
    second = await run_role(inventory, hosts)

    assert changed_entries(second) == []
    log = second.merged().log
    assert not any(HANDLER_NAME in e.name for e in log)


# --- the scoped-out knobs raise (D11) ------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"userparameters": [{"name": "x"}]}, "out of scope"),
        ({"listeninterface": "eth0"}, "listeninterface"),
        ({"docker": True}, "Docker"),
        ({"tlspsk_auto": True}, "TLS-PSK"),
        ({"firewall_enable": True}, "iptables"),
        ({"firewalld_enable": True}, "firewalld"),
        ({"api_create_hosts": True}, "#72"),
        ({"api_create_hostgroup": True}, "#72"),
    ],
)
async def test_scoped_out_knobs_raise(rig, canned_fetch, overrides, expected):
    """A knob whose branch the migration does not carry refuses loudly (D11)."""
    inventory, hosts = rig
    inventory[0]["roles"]["zabbix_agent"].update(overrides)
    with pytest.raises(ValueError, match=expected):
        await run_role(inventory, hosts)


async def test_api_values_are_rejected_as_unknown(rig):
    """The API-only variables never reach the model: the typo rule rejects them."""
    inventory, _hosts = rig
    inventory[0]["roles"]["zabbix_agent"]["zabbix_url"] = "https://zabbix.example.com"
    with pytest.raises(RoleVarsError, match="zabbix_url"):
        resolve_vars(ZabbixAgentVars, "zabbix_agent", inventory)


async def test_unknown_signing_key_raises_with_hint(rig, canned_fetch):
    """A (version, codename) pair the frozen table misses fails loudly (D4)."""
    inventory, hosts = rig
    inventory[0]["roles"]["zabbix_agent"]["distribution_release"] = "noble"
    with pytest.raises(ValueError, match="no Zabbix signing key"):
        await run_role(inventory, hosts)


# --- opt-in branches on the default path ---------------------------------------


async def test_distribution_release_override_skips_the_release_barrier(
    rig, canned_fetch
):
    """The codename override removes wave 0: the body never reads LsbRelease."""
    inventory, hosts = rig
    inventory[0]["roles"]["zabbix_agent"]["distribution_release"] = "focal"
    report = await run_role(inventory, hosts)

    records = [record for record in report.records if record.host == "ubuntu"]
    first_barrier = next(record for record in records if record.barrier)
    assert first_barrier.wave == 0
    assert "LsbRelease" not in report.tree()
    assert DEB_LINE in host_repo_text(hosts["ubuntu"]).splitlines()


def host_repo_text(host: SimulatedHost) -> str:
    return host.remote_files[MANAGED_LIST].decode()


async def test_repo_without_zabbix_skips_the_vendor_block(rig, canned_fetch):
    """``repo != "zabbix"`` on *every* host: no key fetch, no keyring, no lines.

    The factory's fetch gate reads all hosts' resolved vars (it cannot know
    families), so the skipped host's defaults must be overridden too -- the
    one place the gate is coarser than the per-body ``using_repo`` check.
    """
    inventory, hosts = rig
    inventory[0]["roles"]["zabbix_agent"]["repo"] = "distro"
    inventory[1]["roles"] = {"zabbix_agent": {"repo": "distro"}}
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    assert canned_fetch == []
    assert REPO_KEYRING not in host.remote_files
    assert MANAGED_LIST not in host.remote_files
    assert "Debian | Install gpg key" not in entry_names(report)
    # The packages still install (from the distro's own archive now).
    assert "zabbix-agent" in host.apt_packages


async def test_apt_priority_writes_the_pin(rig, canned_fetch):
    """``apt_priority`` lands the preferences file, in its own request pair."""
    inventory, hosts = rig
    inventory[0]["roles"]["zabbix_agent"]["apt_priority"] = 600
    await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    pin = f"{APT_PREFERENCES_DIR}/zabbix-agent-4_4"
    assert host.remote_files[pin].decode() == (
        "Package: zabbix-agent\n"
        "Pin: origin repo.zabbix.com\n"
        "Pin-Priority: 600\n"
    )
    assert host.file_stats[pin]["mode"] == 644


async def test_proxy_variables_ride_the_apt_requests(rig, canned_fetch):
    """D5: ``op``'s ``env`` meta-parameter carries exactly the set pairs."""
    inventory, hosts = rig
    inventory[0]["roles"]["zabbix_agent"].update(
        {"http_proxy": "http://proxy:3128", "https_proxy": "http://proxy:3128"}
    )
    report = await run_role(inventory, hosts)

    pairs = (("http_proxy", "http://proxy:3128"), ("https_proxy", "http://proxy:3128"))
    assert request_attr(report, "Debian | Installing deb repository Ubuntu", "env") == pairs
    assert request_attr(report, "Debian | Installing zabbix-agent", "env") == pairs
    # The keyring request never carried an environment in the role either.
    assert request_attr(report, "Debian | Install gpg key", "env") == ()


async def test_tls_psk_file_is_placed_and_notifies(rig, canned_fetch):
    """The PSK pair: directory + secret file, the file notifying the handler."""
    inventory, hosts = rig
    inventory[0]["roles"]["zabbix_agent"].update(
        {
            "tlspskfile": "/etc/zabbix/tls_psk.txt",
            "tlspsk_secret": "b64secretvalue==",
            "tlspskidentity": "PSK_IDENTITY",
        }
    )
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    assert host.remote_files["/etc/zabbix/tls_psk.txt"] == b"b64secretvalue=="
    assert host.file_stats["/etc/zabbix/tls_psk.txt"] == {
        "user": "zabbix",
        "group": "zabbix",
        "mode": 400,
        "size": len(b"b64secretvalue=="),
    }
    assert "TLSPSKIdentity=PSK_IDENTITY" in host.remote_files[CONF_PATH].decode()
    names = [e.name for e in changed_entries(report)]
    assert "Place TLS PSK File" in names
    assert HANDLER_NAME in names


async def test_selinux_user_var_installs_policycoreutils(rig, canned_fetch):
    """D9's variable half: policycoreutils rides the install wave, and the
    permissive domain follows on the user variable alone."""
    inventory, hosts = rig
    inventory[0]["roles"]["zabbix_agent"]["selinux"] = True
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    assert POLICYCOREUTILS_PACKAGE in host.apt_packages
    assert SELINUX_DOMAIN in host.selinux_permissive_domains
    # The policycoreutils request sits in the install wave, before the probe.
    records = [record for record in report.records if record.host == "ubuntu"]
    policy_wave = next(
        record.wave
        for record in records
        if record.planned is not None
        and record.planned.request.name == "Install policycoreutils-python"
    )
    assert policy_wave == 2


async def test_selinux_probe_alone_opens_the_permissive_gate(rig, canned_fetch):
    """D9's probe half: an Enforcing host gets the exemption without the
    user variable -- and no policycoreutils install (the probe ran too late
    for that task, exactly as in the role)."""
    inventory, hosts = rig
    hosts["ubuntu"].shell_results[GETENFORCE_SHELL] = {
        "exit_status": 0,
        "stdout": "Enforcing\n",
        "stderr": "",
    }
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    assert SELINUX_DOMAIN in host.selinux_permissive_domains
    assert POLICYCOREUTILS_PACKAGE not in host.apt_packages
    assert "Install policycoreutils-python" not in entry_names(report)


# --- the trees ------------------------------------------------------------------


FIRST_PASS_TREE = """(root)
  wave 0
    barrier: LsbRelease → {'id': 'Ubuntu', 'description': 'Ubuntu 20.04.6 LTS', 'release': '20.04', 'codename': 'focal'}
  wave 1
    barrier: FindFiles → ['/etc/apt/sources.list.d/repo_zabbix_com_zabbix_4_2_ubuntu.list']
  wave 2
    ubuntu: Debian | Install gpg key [changed: install GPG key from <stream> to /etc/apt/trusted.gpg.d/zabbix-official-repo.gpg]
        $ scp <stream> -> /tmp/reemote-apt-key-860522ac1c657487/key.gpg
        $ sudo sh -c 'mkdir -p /etc/apt/trusted.gpg.d && if grep -q '"'"'BEGIN PGP PUBLIC KEY BLOCK'"'"' /tmp/reemote-apt-key-860522ac1c657487/key.gpg; then gpg --batch --yes --dearmor -o /etc/apt/trusted.gpg.d/zabbix-official-repo.gpg /tmp/reemote-apt-key-860522ac1c657487/key.gpg; else cp /tmp/reemote-apt-key-860522ac1c657487/key.gpg /etc/apt/trusted.gpg.d/zabbix-official-repo.gpg; fi && chmod 0644 /etc/apt/trusted.gpg.d/zabbix-official-repo.gpg && rm -rf /tmp/reemote-apt-key-860522ac1c657487'
    ubuntu: Debian | Remove unecessary zabbix repository /etc/apt/sources.list.d/repo_zabbix_com_zabbix_4_2_ubuntu.list [changed: /etc/apt/sources.list.d/repo_zabbix_com_zabbix_4_2_ubuntu.list present, will remove]
        $ sudo rm -f /etc/apt/sources.list.d/repo_zabbix_com_zabbix_4_2_ubuntu.list
    ubuntu: Debian | Installing deb repository Ubuntu [changed: apt repo "deb http://repo.zabbix.com/zabbix/4.4/ubuntu focal main" missing, will be added to /etc/apt/sources.list.d/repo_zabbix_com_zabbix_4_4_ubuntu.list]
        $ scp <apt sources> -> /tmp/reemote-apt-repo-edbe8a38730fa32b/repo
        $ sudo sh -c 'mkdir -p /etc/apt/sources.list.d && cp /tmp/reemote-apt-repo-edbe8a38730fa32b/repo /etc/apt/sources.list.d/repo_zabbix_com_zabbix_4_4_ubuntu.list && chmod 0644 /etc/apt/sources.list.d/repo_zabbix_com_zabbix_4_4_ubuntu.list && rm -rf /tmp/reemote-apt-repo-edbe8a38730fa32b'
    ubuntu: Debian | Installing deb-src repository Ubuntu [changed: apt repo "deb-src http://repo.zabbix.com/zabbix/4.4/ubuntu focal main" missing, will be added to /etc/apt/sources.list.d/repo_zabbix_com_zabbix_4_4_ubuntu.list; apt update]
        $ scp <apt sources> -> /tmp/reemote-apt-repo-edbe8a38730fa32b/repo
        $ sudo sh -c 'mkdir -p /etc/apt/sources.list.d && cp /tmp/reemote-apt-repo-edbe8a38730fa32b/repo /etc/apt/sources.list.d/repo_zabbix_com_zabbix_4_4_ubuntu.list && chmod 0644 /etc/apt/sources.list.d/repo_zabbix_com_zabbix_4_4_ubuntu.list && rm -rf /tmp/reemote-apt-repo-edbe8a38730fa32b'
        $ sudo apt-get update
    ubuntu: Debian | Installing zabbix-agent [changed: 3 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install --no-install-recommends zabbix-agent zabbix-sender zabbix-get
    ubuntu: Debian | Enable the service [changed: zabbix-agent.service active state left alone; zabbix-agent.service not enabled, will enable]
        $ sudo systemctl enable zabbix-agent.service
    barrier: Collect getenforce output → {'exit_status': 1, 'stdout': '', 'stderr': ''}
  wave 3
    ubuntu: Configure zabbix-agent   [changed: content /etc/zabbix/zabbix_agentd.conf is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-13b7b99a297f1e81/content
        $ sudo sh -c 'mkdir -p /etc/zabbix && cp /tmp/reemote-install-13b7b99a297f1e81/content /etc/zabbix/zabbix_agentd.conf && chown root:root /etc/zabbix/zabbix_agentd.conf && chmod 644 /etc/zabbix/zabbix_agentd.conf && rm -rf /tmp/reemote-install-13b7b99a297f1e81'
    ubuntu: Create include dir zabbix-agent [changed: /etc/zabbix/zabbix_agentd.d missing, will create]
        $ sudo mkdir -p /etc/zabbix/zabbix_agentd.d
        $ sudo chmod 750 /etc/zabbix/zabbix_agentd.d
        $ sudo chown root:zabbix /etc/zabbix/zabbix_agentd.d
    ubuntu: Make sure the zabbix-agent service is running [changed: zabbix-agent.service not running (None), will start; zabbix-agent.service already enabled]
        $ sudo systemctl start zabbix-agent.service
    barrier: configure done → 1 host notified
  wave 4
    ubuntu: Restart zabbix-agent (handler) [changed: zabbix-agent.service already running; zabbix-agent.service will restart]
        $ sudo systemctl restart zabbix-agent.service"""

CONVERGED_TREE = """(root)
  wave 0
    barrier: LsbRelease → {'id': 'Ubuntu', 'description': 'Ubuntu 20.04.6 LTS', 'release': '20.04', 'codename': 'focal'}
  wave 1
    barrier: FindFiles → ['/etc/apt/sources.list.d/repo_zabbix_com_zabbix_4_4_ubuntu.list']
  wave 2
    ubuntu: Debian | Install gpg key [no change: GPG key A14FE591 already in an apt keyring]
    ubuntu: Debian | Installing deb repository Ubuntu [no change: apt repo "deb http://repo.zabbix.com/zabbix/4.4/ubuntu focal main" exists]
    ubuntu: Debian | Installing deb-src repository Ubuntu [no change: apt repo "deb-src http://repo.zabbix.com/zabbix/4.4/ubuntu focal main" exists]
    ubuntu: Debian | Installing zabbix-agent [no change: all packages present]
    ubuntu: Debian | Enable the service [no change: zabbix-agent.service active state left alone; zabbix-agent.service already enabled]
    barrier: Collect getenforce output → {'exit_status': 1, 'stdout': '', 'stderr': ''}
  wave 3
    ubuntu: Configure zabbix-agent   [no change: content already up to date (/etc/zabbix/zabbix_agentd.conf)]
    ubuntu: Create include dir zabbix-agent [no change: directory /etc/zabbix/zabbix_agentd.d already exists]
    ubuntu: Make sure the zabbix-agent service is running [no change: zabbix-agent.service already running; zabbix-agent.service already enabled]
    barrier: configure done → 0 hosts notified"""

DRY_PREVIEW_TREE = """(root)
  wave 0
    barrier: LsbRelease → unknown until run"""


async def test_tree_snapshot(rig, canned_fetch):
    """The whole run as one tree: two facts, the probe, the checkpoint, handler.

    The second tree is what a rerun prints -- one wave fewer, the handler wave
    simply gone -- and it is the preview ``dry`` deliberately refuses to fake
    (§16.6), whose own tree stops at the first barrier.
    """
    inventory, hosts = rig
    first = await run_role(inventory, hosts)
    assert first.tree() == FIRST_PASS_TREE

    for host in hosts.values():
        if host.name == "ubuntu":
            settle(host)
    second = await run_role(inventory, hosts)
    assert second.tree() == CONVERGED_TREE


async def test_dry_preview_stops_at_the_first_barrier(rig, canned_fetch):
    """The dry pass fetched the key (async factory IO is unconditional) but
    parked at LsbRelease, so it planned nothing and mutated nothing."""
    inventory, hosts = rig
    before = dict(hosts["ubuntu"].remote_files)
    report = await run_role(inventory, hosts, dry=True)
    assert report.tree() == DRY_PREVIEW_TREE
    assert canned_fetch == ["http://repo.zabbix.com/zabbix-official-repo.key"]
    assert hosts["ubuntu"].remote_files == before
