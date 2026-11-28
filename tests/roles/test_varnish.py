# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``varnish`` role (§17.5).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, varnish(v), host_factory=...)``. The factory is plain
(non-``async``) and the host objects persist across passes through the factory
closure, which is what lets the second pass prove convergence.

The load-bearing assertion is the migration's shape claim: **one** fact barrier
and **one** checkpoint feeding **one** handler -- ``fact(LinuxDistribution)``
then setup + configure + services closed by ``checkpoint("configure done")``
then the restart handler. Ansible flushed both of the role's handlers at the end
of the play, so a single report is the faithful notification read (unlike the
docker role, which needs two checkpoints because of its mid-play flush). That
shape is read straight out of ``report.records``.

Two simulation needs are worth naming. The packagecloud signing key is fetched
control-side by ``apt.sources_file``' ``signed_by`` URL form, so the
``key_fetch`` fixture monkeypatches **``apt._fetch_url_bytes``** (the apt
namespace's own seam) with canned armored bytes and records the URLs, keeping
the suite offline -- the docker precedent. And the base vars set
``version="7.6"``/``listen_port=6081`` rather than the role's own defaults
(``6.6``/``80``): the vendor publishes ``varnish66`` for ``focal`` only, and the
example's shared host has port 80 taken. The defaults themselves are exercised
by the pure-helper tests below.

The tree snapshots are pinned from a real simulated run; the spool directory
hashes derive from the destination paths, so they are stable across runs.
"""

import pytest

import reemote.modules.apt.apt as apt_module
from reemote import run_waves
from reemote.modules.server import LinuxDistribution
from reemote.roles import VarnishVars, varnish
from reemote.roles.varnish import (
    DEPENDENCY_PACKAGES,
    NOTIFY_SECRET,
    NOTIFY_SYSVINIT,
    NOTIFY_UNIT,
    NOTIFY_VCL,
    SYSVINIT_PATH,
    UNIT_PATH,
    distribution_codename,
    distribution_segment,
    notifying_names,
    packagecloud_repo,
    render_default_vcl,
    render_secret,
    render_sysvinit,
    render_unit,
    varnish_plan,
    version_compare,
    version_operands,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from reemote.stages import CheckpointRequest, FactRequest
from tests.simulated_host import SimulatedHost

SOURCES_PATH = "/etc/apt/sources.list.d/varnishcache_varnish76.sources"
KEYRING_PATH = "/etc/apt/keyrings/varnishcache_varnish76.asc"
SYSVINIT_NAME = "Copy Varnish configuration (sysvinit)"
VCL_PATH = "/etc/varnish/default.vcl"
SECRET_PATH = "/etc/varnish/secret"
HANDLER_NAME = "Restart varnish (handler)"
#: The unit the vendor package ships, modelled as already loaded so the role's
#: ``/etc`` override is a shadowing file rather than the unit's first fragment
#: (issue #75 -- see :func:`seed`).
PACKAGED_UNIT_PATH = "/usr/lib/systemd/system/varnish.service"
KEY_BYTES = b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nfake\n-----END PGP PUBLIC KEY BLOCK-----\n"

#: The base role variables, as the inventory's ``roles.varnish`` mapping.
#: ``version``/``listen_port`` are the two values a modern Ubuntu host has to
#: override anyway (see the module docstring); the backend and vhost entries
#: keep both optional ``default.vcl`` loops live.
BASE_VARS = {
    "version": "7.6",
    "listen_port": 6081,
    "backends": {"apache": {"host": "10.0.2.2", "port": 80}},
    "vhosts": {"example.com": {"backend": "apache"}},
}

EXPECTED_SOURCES = (
    "Types: deb\n"
    "URIs: https://packagecloud.io/varnishcache/varnish76/ubuntu\n"
    "Suites: noble\n"
    "Components: main\n"
    f"Signed-By: {KEYRING_PATH}\n"
)

UBUNTU_2404 = {
    "name": "Ubuntu",
    "family": "Debian",
    "major": "24",
    "minor": "04",
    "release_meta": {
        "ID": "ubuntu",
        "VERSION_ID": "24.04",
        "VERSION_CODENAME": "noble",
    },
}


@pytest.fixture
def key_fetch(monkeypatch):
    """Make the ``signed_by`` URL fetch offline and observable.

    ``apt.sources_file`` fetches the ASCII-armored key control-side through the
    apt namespace's own ``_fetch_url_bytes`` seam; this stands in for the
    network and records every URL asked for, so a test can assert the key was
    fetched once on pass one and again on the converged pass (content
    idempotence needs the bytes to compare) but never when the repository is
    switched off.
    """
    fetched: list[str] = []

    def fake(url: str) -> bytes:
        fetched.append(url)
        return KEY_BYTES

    monkeypatch.setattr(apt_module, "_fetch_url_bytes", fake)
    return fetched


def make_inventory(**overrides) -> list[dict]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride in the entry's ``roles`` mapping under
    ``"varnish"``, bound by ``resolve_vars``; the overrides drive the
    non-default branches.
    """
    entry = {
        "host": "ubuntu",
        "family": "Ubuntu",
        "connection": {
            "address": "192.0.2.10",
            "username": "supervisor",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "ubuntu",
        "tags": ["ubuntu_host", "apt_package_manager"],
        "roles": {"varnish": dict(BASE_VARS)},
    }
    entry["roles"]["varnish"].update(overrides)
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
    """Pre-state: a stock Ubuntu 24.04 machine with no Varnish configured yet.

    Only ``/tmp`` and the vendor package's own unit file exist besides (the
    install spools land in ``/tmp``), so every ``files`` request on the first
    pass is a genuine creation; the distribution read answers the role's only
    barrier. The apt stamp starts unset so the chained index refresh is
    observable.

    The packaged unit is seeded twice over -- on the simulated disk **and** in
    ``systemd_loaded_fragments`` -- because that is the state a real host is in
    when this role runs: systemd has ``/usr/lib/systemd/system/varnish.service``
    loaded, and the role writes a higher-precedence file next to it. With both
    halves the offline read decides the reload the way the live host does, by the
    override *outranking* the loaded fragment (issue #75) rather than by mere
    staleness, so the suite would have failed on the pre-#75 gate. The apt store
    stays empty because the test observes the role's own install.
    """
    host.linux_distribution = dict(UBUNTU_2404)
    host.apt_packages = {}
    host.apt_update_stamp = None
    host.services = {}
    host.enabled_services = set()
    host.systemd_reload_pending = set()
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}
    host.directory_stats = {"/tmp": {"mode": 1777, "user": "root", "group": "root"}}
    host.existing_files |= set(host.directory_stats)
    host.systemd_loaded_fragments = {"varnish.service": PACKAGED_UNIT_PATH}
    host.existing_files.add(PACKAGED_UNIT_PATH)


def make_hosts(inventory: list[dict]) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets."""
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            seed(host)
        hosts[host.name] = host
    return hosts


async def run_role(
    inventory: list[dict], hosts: dict[str, SimulatedHost], *, dry: bool = False
):
    """Resolve variables, build the sync factory's deploy, and run one wave pass."""
    v = resolve_vars(VarnishVars, "varnish", inventory)

    def factory(entry: dict) -> SimulatedHost:
        # The *same* host object every pass, so state survives the barriers.
        return hosts[entry["host"]]

    # A plain (non-async) factory: called, not awaited.
    deploy = varnish(v)
    report, _hosts = await run_waves(inventory, deploy, host_factory=factory, dry=dry)
    return report


def changed_entries(report) -> list:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def ubuntu_names(report) -> set[str]:
    """Every Ubuntu log entry name of one pass."""
    return {entry.name for entry in report.merged().log if entry.host == "ubuntu"}


def ubuntu_log(report) -> dict[str, object]:
    """The Ubuntu host's log entries of one pass, keyed by name."""
    return {
        entry.name: entry for entry in report.merged().log if entry.host == "ubuntu"
    }


def ubuntu_vars(**overrides) -> VarnishVars:
    """The base inventory variables as a :class:`VarnishVars`, for pure tests."""
    return VarnishVars(**{**BASE_VARS, **overrides})


def plan_notify_names(**overrides) -> set[str]:
    """The notification set the plan for those variables derives."""
    return set(varnish_plan(ubuntu_vars(**overrides), dict(UBUNTU_2404)).notify_names)


def _kind(barrier) -> tuple:
    """Classify one barrier record for the shape assertion.

    A ``fact`` barrier names the fact class it reads; a ``checkpoint`` names
    itself. An ``act``/``control`` barrier would fall through to a distinct
    label, so a speculatively-added barrier breaks the shape test rather than
    hiding.
    """
    if isinstance(barrier, FactRequest):
        return ("fact", barrier.fact_cls)
    if isinstance(barrier, CheckpointRequest):
        return ("checkpoint", barrier.name)
    return ("other", type(barrier).__name__)


def _barrier_shape(report):
    """The ubuntu host's ordered ``(wave, kind)`` barrier list for one pass."""
    ubuntu = [r for r in report.records if r.host == "ubuntu"]
    return [(r.wave, _kind(r.barrier)) for r in ubuntu if r.barrier is not None]


def _request_waves(report) -> set[int]:
    """The set of wave numbers that carried at least one ubuntu request."""
    return {
        r.wave
        for r in report.records
        if r.host == "ubuntu" and r.log is not None
    }


# --- the stream shape ---------------------------------------------------------


async def test_stream_is_one_fact_and_one_checkpoint(key_fetch):
    """fact -> (setup + configure + services) + checkpoint -> handler.

    Exactly one checkpoint: the whole task list is a single notification wave,
    so the handler reads one report (D1/D4). A second checkpoint, or a handler
    wired to ``service_handler`` with a whole-inventory name union, breaks this.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert _barrier_shape(report) == [
        (0, ("fact", LinuxDistribution)),
        (1, ("checkpoint", "configure done")),
    ]
    # Requests land in two waves: the task list (1), the restart handler (2).
    assert _request_waves(report) == {1, 2}

    # The rerun re-derives every barrier; only the handler wave disappears.
    second = await run_role(inventory, hosts)
    assert _barrier_shape(second) == _barrier_shape(report)
    assert _request_waves(second) == {1}


async def test_requests_run_in_the_roles_task_order(key_fetch):
    """The wave's log lines follow ``tasks/main.yml``'s own ordering."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert [entry.name for entry in changed_entries(report)] == [
        "Ensure dependencies are present",
        "Add packagecloud.io Varnish apt repository",
        "Ensure Varnish is installed",
        "Ensure Varnish config path exists",
        SYSVINIT_NAME,
        "Copy override for systemd unit",
        "Copy Varnish default VCL",
        "Copy varnish secret",
        "Ensure varnish is started and enabled at boot",
        HANDLER_NAME,
    ]
    # The four metadata companions ran too and reported no change: the install
    # they follow had just created the file with the same root:root 0644.
    assert ubuntu_names(report) - {entry.name for entry in changed_entries(report)} == {
        "Set Varnish configuration attributes (sysvinit)",
        "Set Varnish systemd unit attributes",
        "Set Varnish default VCL attributes",
        "Set varnish secret attributes",
    }


# --- the first pass really deploys the role -----------------------------------


async def test_first_pass_installs_and_configures_varnish(key_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    # The deb822 sources landed exactly as the role's apt_repository line reads
    # (D1), and the key under the repo's own filename, armored, 0644.
    assert host.remote_files[SOURCES_PATH].decode() == EXPECTED_SOURCES
    assert host.remote_files[KEYRING_PATH] == KEY_BYTES
    assert host.file_stats[KEYRING_PATH]["mode"] == 644
    assert key_fetch == ["https://packagecloud.io/varnishcache/varnish76/gpgkey"]

    # Dependencies plus the package the version selects.
    for package in (*DEPENDENCY_PACKAGES, "varnish"):
        assert package in host.apt_packages

    # Every rendered file is the control-side render, root:root 0644 (D6).
    hv = ubuntu_vars()
    assert host.remote_files[SYSVINIT_PATH] == render_sysvinit(hv).encode()
    assert host.remote_files[UNIT_PATH] == render_unit(hv).encode()
    assert host.remote_files[VCL_PATH] == render_default_vcl(hv).encode()
    assert host.remote_files[SECRET_PATH] == render_secret(hv).encode()
    for path in (SYSVINIT_PATH, UNIT_PATH, VCL_PATH, SECRET_PATH):
        assert host.file_stats[path] == {
            "user": "root",
            "group": "root",
            "mode": 644,
            "size": len(host.remote_files[path]),
        }, path
    assert host.directory_stats["/etc/systemd/system"]["mode"] == 755

    # The service is running and enabled, and the config change queued the
    # handler, which restarted the unit.
    assert host.services["varnish.service"] == "active"
    assert "varnish.service" in host.enabled_services
    log = ubuntu_log(report)
    assert log[NOTIFY_SYSVINIT].changed
    assert log[HANDLER_NAME].changed


async def test_the_unit_override_queues_the_reload_inside_the_restart(key_fetch):
    """D4: the folded handler reloads for the shadowing override, and only then.

    Upstream keeps ``reload systemd`` as a separate handler; here it rides the
    restart as ``daemon_reload=True``, whose ``SystemdNeedDaemonReload`` gate
    unions staleness with the load-root comparison issue #75 added. The seeded
    host has the packaged fragment loaded, so the role's fresh
    ``/etc/systemd/system/varnish.service`` is pending by *outranking* it and the
    one handler request yields ``daemon-reload`` then ``restart``. A change to a
    non-unit file leaves the override the loaded fragment, so the restart runs
    without a reload, and the converged pass runs neither.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    first_log = ubuntu_log(first)

    assert first_log[HANDLER_NAME].reason == (
        "daemon-reload; varnish.service already running; varnish.service will restart"
    )
    # The reload is a sub-command of the restart request, never a line of its own.
    assert "Reload systemd (handler)" not in first_log

    # A secret-only change restarts Varnish; the pass-1 reload made the override
    # the loaded fragment, so the gate stays shut and no reload rides it.
    inventory[0]["roles"]["varnish"]["secret"] = "b0b8a2c4-1111-2222-3333-444455556666"
    second = await run_role(inventory, hosts)
    second_log = ubuntu_log(second)
    assert second_log[NOTIFY_SECRET].changed
    assert "daemon-reload" not in second_log[HANDLER_NAME].reason
    assert "will restart" in second_log[HANDLER_NAME].reason

    # Converged: no handler request at all.
    third = await run_role(inventory, hosts)
    third_log = ubuntu_log(third)
    assert HANDLER_NAME not in third_log


# --- the second pass is a no-op -------------------------------------------------


async def test_second_pass_is_a_noop(key_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    # Nothing changed anywhere -- repo and keyring in place, package present,
    # all four files byte-identical with their metadata, service up and enabled
    # -- so the single checkpoint notified nobody and the handler wave vanished.
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert HANDLER_NAME not in ubuntu_names(second)
    # The converged pass still reads the key back to compare it (content
    # idempotence), but writes neither the keyring nor the sources file.
    assert key_fetch == [
        "https://packagecloud.io/varnishcache/varnish76/gpgkey",
        "https://packagecloud.io/varnishcache/varnish76/gpgkey",
    ]


async def test_non_ubuntu_host_is_never_touched(key_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    skipped = hosts["web1"]

    # The family guard returns before the first yield, so the off-target host
    # is in no wave, emits no log line and its stores never move.
    assert not any(entry.host == "web1" for entry in report.merged().log)
    assert not any(r.host == "web1" for r in report.records)
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}
    assert skipped.services == {}


async def test_dry_preview_stops_at_the_distribution_barrier(key_fetch):
    """``dry=True`` never runs a barrier, so it parks on the first item.

    varnish's first yielded item is the ``LinuxDistribution`` ``fact`` barrier,
    and nothing downstream is buildable without the codename it answers, so the
    preview plans no request, applies nothing and fetches no key.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts, dry=True)

    assert host.apt_update_stamp is None
    assert host.remote_files == {}
    assert host.apt_packages == {}
    assert host.services == {}
    assert changed_entries(report) == []
    assert _barrier_shape(report) == [(0, ("fact", LinuxDistribution))]
    assert _request_waves(report) == set()
    assert key_fetch == []


# --- the notification set -------------------------------------------------------


async def test_a_config_change_alone_queues_the_handler(key_fetch):
    """The handler fires from a rendered-file change with nothing else moving."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    inventory[0]["roles"]["varnish"]["secret"] = "0d1f5a01-1111-2222-3333-444455556666"
    second = await run_role(inventory, hosts)
    log = ubuntu_log(second)

    assert log["Copy varnish secret"].changed
    assert log[NOTIFY_SYSVINIT].changed is False
    assert log[HANDLER_NAME].changed
    assert hosts["ubuntu"].remote_files[SECRET_PATH] == b"0d1f5a01-1111-2222-3333-444455556666\n"
    # The service loop ran first and had nothing to do; the handler restarted.
    assert log["Ensure varnish is started and enabled at boot"].changed is False


async def test_a_package_change_alone_does_not_notify(key_fetch):
    """The setup stage is deliberately outside the notification set.

    ``tasks/setup-Debian.yml``'s ``apt`` tasks carry no ``notify``, so a reinstall
    changes a request without queueing the restart -- mirroring Ansible exactly.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    hosts["ubuntu"].apt_packages.pop("varnish")
    second = await run_role(inventory, hosts)
    log = ubuntu_log(second)

    assert log["Ensure Varnish is installed"].changed
    assert HANDLER_NAME not in log


async def test_attribute_drift_notifies_through_its_companion(key_fetch):
    """D6: ``files.install`` is content-only, so the metadata pair catches mode drift."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    hosts["ubuntu"].file_stats[UNIT_PATH]["mode"] = 600
    second = await run_role(inventory, hosts)
    log = ubuntu_log(second)

    assert log["Copy override for systemd unit"].changed is False
    assert log["Set Varnish systemd unit attributes"].changed
    assert log[HANDLER_NAME].changed
    assert hosts["ubuntu"].file_stats[UNIT_PATH]["mode"] == 644


# --- the branches ----------------------------------------------------------------


async def test_use_default_vcl_false_skips_the_vcl_pair(key_fetch):
    # The role's ``when: varnish_use_default_vcl`` -- both the request and its
    # notification name disappear.
    inventory = make_inventory(use_default_vcl=False)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    assert VCL_PATH not in host.remote_files
    assert NOTIFY_VCL not in ubuntu_names(report)
    assert "Set Varnish default VCL attributes" not in ubuntu_names(report)
    assert plan_notify_names(use_default_vcl=False) == set(plan_notify_names()) - {
        NOTIFY_VCL,
        "Set Varnish default VCL attributes",
    }
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_apt_use_packagecloud_false_uses_the_distro_package(key_fetch):
    # Both packagecloud blocks sit behind the role's ``when:``, so neither the
    # dependencies nor the repository (and therefore no key fetch) is managed.
    inventory = make_inventory(apt_use_packagecloud=False)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    assert key_fetch == []
    assert SOURCES_PATH not in host.remote_files
    assert "Ensure dependencies are present" not in ubuntu_names(report)
    assert "Add packagecloud.io Varnish apt repository" not in ubuntu_names(report)
    assert "Ensure Varnish is installed" in ubuntu_names(report)
    assert host.remote_files[UNIT_PATH]  # configure still runs in full
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_modules_package_installs_when_set(key_fetch):
    inventory = make_inventory(modules_package_name="varnish-modules")
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert ubuntu_log(report)["Ensure Varnish VMODs are installed"].changed
    assert "varnish-modules" in hosts["ubuntu"].apt_packages
    # It is a setup task, so it does not notify (its name stays out of the set).
    assert "Ensure Varnish VMODs are installed" not in plan_notify_names()
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_enabled_services_loops_over_every_unit(key_fetch):
    # D5: the variable is honored, one request per entry -- even though
    # upstream's own ``when:`` makes this loop dead code on Debian family.
    inventory = make_inventory(enabled_services=["varnish", "varnishncsa"])
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)
    log = ubuntu_log(report)

    assert log["Ensure varnish is started and enabled at boot"].changed
    assert log["Ensure varnishncsa is started and enabled at boot"].changed
    assert host.enabled_services == {"varnish.service", "varnishncsa.service"}
    assert HANDLER_NAME in log  # the config writes notified it regardless
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the pure helpers, unit-tested directly --------------------------------------


def test_packagecloud_repo_flattens_the_version():
    assert packagecloud_repo("6.6") == "varnish66"
    assert packagecloud_repo("7.6") == "varnish76"
    assert packagecloud_repo("4") == "varnish4"


def test_distribution_reads():
    assert distribution_codename(UBUNTU_2404) == "noble"
    assert distribution_segment(UBUNTU_2404) == "ubuntu"
    # An uppercase ``name`` still lower-cases into the URL segment.
    assert distribution_segment({"name": "Ubuntu"}) == "ubuntu"
    with pytest.raises(ValueError, match="VERSION_CODENAME"):
        distribution_codename({"name": "Ubuntu"})
    with pytest.raises(ValueError, match="VERSION_CODENAME"):
        distribution_codename({"release_meta": {"VERSION_CODENAME": ""}})


def test_version_compare_is_the_ansible_version_test():
    assert version_operands("7.6") == (7, 6)
    assert version_operands("4.0.1") == (4, 0, 1)
    assert version_compare("4.0", "4.0", eqln=">=")
    # The two gates the shipped templates actually use.
    assert not version_compare("7.6", "4.1", eqln="<")
    assert version_compare("4", "4.1", eqln="<")
    assert version_compare("4.1", "4.0", eqln=">=")
    with pytest.raises(ValueError, match="unknown version comparison"):
        version_compare("4.0", "4.0", eqln="=<")


def active_daemon_opts(text: str) -> str:
    """The live ``DAEMON_OPTS`` assignment, continuations joined.

    The template also carries two commented-out alternatives that mention
    ``-u varnish -g varnish``, so the version gate has to be read off the one
    assignment the file actually activates.
    """
    lines: list[str] = []
    collecting = False
    for line in text.splitlines():
        if line.startswith("DAEMON_OPTS="):
            collecting = True
        if collecting:
            lines.append(line)
            collecting = not line.rstrip().endswith('"')
    return "\n".join(lines)


def test_render_sysvinit_follows_the_version_gate():
    modern = render_sysvinit(ubuntu_vars())
    assert "VARNISH_LISTEN_PORT=6081" in modern
    assert "VARNISH_SECRET_FILE=/etc/varnish/secret" in modern
    # ``is version('4.1', '<')`` is false for 7.6, so no -u/-g pair.
    assert "-u varnish -g varnish" not in active_daemon_opts(modern)
    assert 'PIDFILE="/run/varnishd.pid"' in modern

    # A pre-4.1 version re-gains the user/group pair; an empty pidfile drops
    # both the PIDFILE line and its comment.
    ancient = render_sysvinit(ubuntu_vars(version="4.0", pidfile=""))
    assert "-u varnish -g varnish" in active_daemon_opts(ancient)
    assert "PIDFILE" not in ancient
    assert "# Varnish PID file" not in ancient


def test_render_unit_carries_the_daemon_arguments():
    unit = render_unit(ubuntu_vars())
    assert unit.startswith("[Unit]\nDescription=Varnish Cache")
    assert "PIDFile=/run/varnishd.pid" in unit
    assert "LimitNOFILE=131072" in unit
    assert (
        "ExecStart=/usr/sbin/varnishd -a :6081 -T 127.0.0.1:6082"
        " -P /run/varnishd.pid -f /etc/varnish/default.vcl"
        " -S /etc/varnish/secret"
        " -s file,/var/lib/varnish/varnish_storage.bin,256M"
        " \n"
    ) in unit

    with_opts = render_unit(ubuntu_vars(varnishd_extra_options="-p workspace_backend=128k"))
    assert "-s file,/var/lib/varnish/varnish_storage.bin,256M -p workspace_backend=128k\n" in (
        with_opts
    )


def test_render_default_vcl_emits_backends_and_vhosts():
    vcl = render_default_vcl(ubuntu_vars())
    assert vcl.startswith("vcl 4.0;\n")
    assert 'backend default {\n  .host = "127.0.0.1";\n  .port = "8080";\n}' in vcl
    assert 'backend apache {\n  .host = "10.0.2.2";\n  .port = "80";\n}' in vcl
    assert (
        'if (req.http.host == "example.com" || req.http.host == "www.example.com") {\n'
        "      set req.backend_hint = apache;\n"
        "  }"
    ) in vcl

    # No ``varnish_backends``/``varnish_vhosts`` at all by default -- the keys are
    # omitted from the context, so the ``is defined`` gates stay closed.
    bare = render_default_vcl(VarnishVars(version="3.0"))
    assert not bare.startswith("vcl 4.0;")
    assert "backend apache" not in bare
    assert "req.http.host" not in bare
    assert "backend default" in bare


def test_render_secret_is_the_value_plus_a_newline():
    assert render_secret(VarnishVars()) == "14bac2e6-1e34-4770-8078-974373b76c90\n"
    assert render_secret(VarnishVars(secret="x")) == "x\n"


def test_notifying_names_follows_the_vcl_gate():
    with_vcl = notifying_names({"vcl_content": "vcl 4.0;\n"})
    assert with_vcl == (
        NOTIFY_SYSVINIT,
        "Set Varnish configuration attributes (sysvinit)",
        NOTIFY_UNIT,
        "Set Varnish systemd unit attributes",
        NOTIFY_VCL,
        "Set Varnish default VCL attributes",
        NOTIFY_SECRET,
        "Set varnish secret attributes",
    )
    assert notifying_names({"vcl_content": None}) == tuple(
        name for name in with_vcl if "VCL" not in name
    )


def test_varnish_plan_derivations():
    plan = varnish_plan(ubuntu_vars(), dict(UBUNTU_2404))
    assert plan.codename == "noble"
    assert plan.repo_name == "varnish76"
    assert plan.repo_url == "https://packagecloud.io/varnishcache/varnish76/ubuntu"
    assert plan.gpg_key_url == "https://packagecloud.io/varnishcache/varnish76/gpgkey"
    assert plan.sources_filename == "varnishcache_varnish76"
    assert plan.use_packagecloud is True
    assert plan.package_name == "varnish"
    assert plan.modules_package_name == ""
    assert plan.vcl_path == VCL_PATH
    assert plan.secret_path == SECRET_PATH
    assert plan.enabled_services == ("varnish",)
    assert len(plan.notify_names) == 8

    # The default version derives the default repo name, and the three
    # overrides win over their derived values exactly as upstream's ``when:``
    # and ``set_fact`` interplay lets them.
    default = varnish_plan(VarnishVars(), dict(UBUNTU_2404))
    assert default.repo_name == "varnish66"
    assert default.sources_filename == "varnishcache_varnish66"

    pinned = varnish_plan(
        VarnishVars(
            packagecloud_repo="varnish75",
            apt_repo_url="https://mirror.example.com/varnish75/ubuntu",
            apt_gpg_key="https://mirror.example.com/varnish75.key",
        ),
        dict(UBUNTU_2404),
    )
    assert pinned.repo_name == "varnish75"
    assert pinned.repo_url == "https://mirror.example.com/varnish75/ubuntu"
    assert pinned.gpg_key_url == "https://mirror.example.com/varnish75.key"
    assert pinned.sources_filename == "varnishcache_varnish75"


def test_plan_without_codename_is_a_hard_error():
    # The repository's Suites would be empty, so the body raises rather than
    # writing an apt-rejected source -- and it raises before any request.
    with pytest.raises(ValueError, match="VERSION_CODENAME"):
        varnish_plan(ubuntu_vars(), {"name": "Ubuntu", "release_meta": {}})


# --- the typed variables reject bad input -----------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"listen_port": 0}, "between 1 and 65535"),
        ({"listen_port": 70000}, "between 1 and 65535"),
        ({"default_backend_port": "not-a-port"}, "Input should be a valid integer"),
        ({"admin_listen_port": "6082"}, None),
        ({"package_name": ""}, "non-empty string"),
        ({"version": ""}, "non-empty string"),
        ({"secret": ""}, "non-empty string"),
        ({"default_vcl_template_path": ""}, "non-empty string"),
        ({"config_path": "etc/varnish"}, "absolute path"),
        ({"config_path": "/etc/varnish/"}, "slash"),
        ({"limit_nofile": 0}, "positive"),
        ({"enabled_services": ["varnish", ""]}, "non-empty strings"),
        ({"backends": {"": {"host": "h", "port": 80}}}, "keys must be non-empty"),
        ({"backends": {"b": {"host": "", "port": 80}}}, "a backend needs a host"),
        ({"backends": {"b": {"host": "h", "port": 0}}}, "between 1 and 65535"),
        ({"vhosts": {"v": {"backend": ""}}}, "a vhost needs a backend name"),
        ({"unknown_knob": True}, "unknown_knob"),
    ],
)
def test_vars_reject_invalid_values(kwargs, match):
    if match is None:
        # A string port is upstream's own spelling and coerces, not fails.
        assert VarnishVars(**kwargs).admin_listen_port == 6082
        return
    # pydantic's ValidationError is a ValueError; so is every validator raise.
    with pytest.raises(ValueError, match=match):
        VarnishVars(**kwargs)


def test_vars_normalization():
    # A list from JSON is normalized to a tuple so every value that reaches
    # op() stays hashable; a string port is upstream's own spelling.
    hv = VarnishVars(enabled_services=["varnish", "varnishncsa"], listen_port="6081")
    assert hv.enabled_services == ("varnish", "varnishncsa")
    assert hv.listen_port == 6081
    assert VarnishVars().version == "6.6"
    assert VarnishVars().listen_port == 80
    assert VarnishVars().backends == {}
    assert VarnishVars().packagecloud_repo is None


def test_typoed_role_variable_is_reported_not_ignored():
    inventory = make_inventory()
    inventory[0]["roles"]["varnish"]["listen_prot"] = 6081
    hosts = make_hosts(inventory)

    with pytest.raises(RoleVarsError, match="listen_prot"):
        resolve_vars(VarnishVars, "varnish", inventory)
    # Nothing ran: the failure is at resolve time, before any host is contacted.
    assert hosts["ubuntu"].remote_files == {}
    assert hosts["web1"].remote_files == {}


# --- the wave tree ------------------------------------------------------------------

# Pinned from a real simulated run (the captured output, verbatim). Three waves
# on the first pass: the ``LinuxDistribution`` barrier, the whole task list, then
# the two handlers it notifies. The converged rerun keeps the barrier and the task
# list -- every line a no-change -- and loses the handler wave entirely.
FIRST_PASS_TREE = """(root)
  wave 0
    barrier: LinuxDistribution → {'name': 'Ubuntu', 'family': 'Debian', 'major': '24', 'minor': '04', 'release_meta': {'ID': 'ubuntu', 'VERSION_ID': '24.04', 'VERSION_CODENAME': 'noble'}}
  wave 1
    ubuntu: Ensure dependencies are present [changed: 2 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install apt-transport-https gnupg2
    ubuntu: Add packagecloud.io Varnish apt repository [changed: signing key from https://packagecloud.io/varnishcache/varnish76/gpgkey will be installed at "/etc/apt/keyrings/varnishcache_varnish76.asc"; apt sources file "/etc/apt/sources.list.d/varnishcache_varnish76.sources" missing or outdated, will be written; apt update]
        $ scp <deb822 sources> -> /tmp/reemote-apt-sources-file-396a029aa29218c9/sources
        $ sudo sh -c 'mkdir -p /etc/apt/keyrings && cp /tmp/reemote-apt-sources-file-396a029aa29218c9/sources /etc/apt/keyrings/varnishcache_varnish76.asc && chmod 0644 /etc/apt/keyrings/varnishcache_varnish76.asc && rm -rf /tmp/reemote-apt-sources-file-396a029aa29218c9'
        $ scp <deb822 sources> -> /tmp/reemote-apt-sources-file-d0f905d49e0c6f56/sources
        $ sudo sh -c 'mkdir -p /etc/apt/sources.list.d && cp /tmp/reemote-apt-sources-file-d0f905d49e0c6f56/sources /etc/apt/sources.list.d/varnishcache_varnish76.sources && chmod 0644 /etc/apt/sources.list.d/varnishcache_varnish76.sources && rm -rf /tmp/reemote-apt-sources-file-d0f905d49e0c6f56'
        $ sudo apt-get update
    ubuntu: Ensure Varnish is installed [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install varnish
    ubuntu: Ensure Varnish config path exists [changed: /etc/systemd/system missing, will create]
        $ sudo mkdir -p /etc/systemd/system
        $ sudo chmod 755 /etc/systemd/system
    ubuntu: Copy Varnish configuration (sysvinit) [changed: content /etc/default/varnish is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-52b9c565e93858b7/content
        $ sudo sh -c 'mkdir -p /etc/default && cp /tmp/reemote-install-52b9c565e93858b7/content /etc/default/varnish && chown root:root /etc/default/varnish && chmod 644 /etc/default/varnish && rm -rf /tmp/reemote-install-52b9c565e93858b7'
    ubuntu: Set Varnish configuration attributes (sysvinit) [no change: file /etc/default/varnish already exists]
    ubuntu: Copy override for systemd unit [changed: content /etc/systemd/system/varnish.service is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-df8eae81e3715cad/content
        $ sudo sh -c 'mkdir -p /etc/systemd/system && cp /tmp/reemote-install-df8eae81e3715cad/content /etc/systemd/system/varnish.service && chown root:root /etc/systemd/system/varnish.service && chmod 644 /etc/systemd/system/varnish.service && rm -rf /tmp/reemote-install-df8eae81e3715cad'
    ubuntu: Set Varnish systemd unit attributes [no change: file /etc/systemd/system/varnish.service already exists]
    ubuntu: Copy Varnish default VCL [changed: content /etc/varnish/default.vcl is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-295b5a9372eb9450/content
        $ sudo sh -c 'mkdir -p /etc/varnish && cp /tmp/reemote-install-295b5a9372eb9450/content /etc/varnish/default.vcl && chown root:root /etc/varnish/default.vcl && chmod 644 /etc/varnish/default.vcl && rm -rf /tmp/reemote-install-295b5a9372eb9450'
    ubuntu: Set Varnish default VCL attributes [no change: file /etc/varnish/default.vcl already exists]
    ubuntu: Copy varnish secret      [changed: content /etc/varnish/secret is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-44517e5febd3a1aa/content
        $ sudo sh -c 'mkdir -p /etc/varnish && cp /tmp/reemote-install-44517e5febd3a1aa/content /etc/varnish/secret && chown root:root /etc/varnish/secret && chmod 644 /etc/varnish/secret && rm -rf /tmp/reemote-install-44517e5febd3a1aa'
    ubuntu: Set varnish secret attributes [no change: file /etc/varnish/secret already exists]
    ubuntu: Ensure varnish is started and enabled at boot [changed: varnish.service not running (None), will start; varnish.service not enabled, will enable]
        $ sudo systemctl start varnish.service
        $ sudo systemctl enable varnish.service
    barrier: configure done → 1 host notified
  wave 2
    ubuntu: Restart varnish (handler) [changed: daemon-reload; varnish.service already running; varnish.service will restart]
        $ sudo systemctl daemon-reload
        $ sudo systemctl restart varnish.service"""

CONVERGED_TREE = """(root)
  wave 0
    barrier: LinuxDistribution → {'name': 'Ubuntu', 'family': 'Debian', 'major': '24', 'minor': '04', 'release_meta': {'ID': 'ubuntu', 'VERSION_ID': '24.04', 'VERSION_CODENAME': 'noble'}}
  wave 1
    ubuntu: Ensure dependencies are present [no change: all packages present]
    ubuntu: Add packagecloud.io Varnish apt repository [no change: apt sources file "/etc/apt/sources.list.d/varnishcache_varnish76.sources" is already configured]
    ubuntu: Ensure Varnish is installed [no change: all packages present]
    ubuntu: Ensure Varnish config path exists [no change: directory /etc/systemd/system already exists]
    ubuntu: Copy Varnish configuration (sysvinit) [no change: content already up to date (/etc/default/varnish)]
    ubuntu: Set Varnish configuration attributes (sysvinit) [no change: file /etc/default/varnish already exists]
    ubuntu: Copy override for systemd unit [no change: content already up to date (/etc/systemd/system/varnish.service)]
    ubuntu: Set Varnish systemd unit attributes [no change: file /etc/systemd/system/varnish.service already exists]
    ubuntu: Copy Varnish default VCL [no change: content already up to date (/etc/varnish/default.vcl)]
    ubuntu: Set Varnish default VCL attributes [no change: file /etc/varnish/default.vcl already exists]
    ubuntu: Copy varnish secret      [no change: content already up to date (/etc/varnish/secret)]
    ubuntu: Set varnish secret attributes [no change: file /etc/varnish/secret already exists]
    ubuntu: Ensure varnish is started and enabled at boot [no change: varnish.service already running; varnish.service already enabled]
    barrier: configure done → 0 hosts notified"""

DRY_PREVIEW_TREE = """(root)
  wave 0
    barrier: LinuxDistribution → unknown until run"""


async def test_tree_snapshot(key_fetch):
    """The whole run as one tree: the barrier wave, the task wave, the handler.

    The second tree is what a rerun prints -- same barriers, every line a
    no-change, no handler wave -- and it is the preview ``dry`` deliberately
    refuses to fake: with the codename unread, the repository request cannot be
    planned at all.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)

    assert first.tree() == FIRST_PASS_TREE

    second = await run_role(inventory, hosts)
    assert second.tree() == CONVERGED_TREE

    dry = await run_role(make_inventory(), make_hosts(make_inventory()), dry=True)
    assert dry.tree() == DRY_PREVIEW_TREE
