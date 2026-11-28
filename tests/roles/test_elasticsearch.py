# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``elasticsearch`` role (§17.5).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, elasticsearch(v), host_factory=...)``. The factory is
plain (non-``async``) -- every input is a resolved variable or a frozen
constant -- so it is called, not awaited. Both key paths -- the Ubuntu
``files.download`` fetch and the RedHat ``control(files.fetch_url)`` barrier --
ride the same ``_fetch_url_bytes`` stub the kubectl tests use.

The load-bearing assertion is the migration's shape claim per family: the
Ubuntu stream is **two waves on both passes** with the checkpoint as the
**only barrier** -- the configure requests, then ``checkpoint("configure
done")``, then the handler (the restart, notified hosts only), the service
task and the port wait. The RedHat stream leads with one **control barrier**
closing an empty wave 0 (the repo_epel shape), then runs the same
checkpoint/tail structure one wave later. Unlike the redis role, the
converged rerun still has *two* request waves -- ``meta: flush_handlers`` sat
between the template tasks and the service task, so the service and wait
follow the checkpoint even when nobody notified. And unlike a state request,
the wait always reports changed (it is a poll, not a gate), which is exactly
why its name is absent from ``NOTIFY_RESTART`` (D6): the second pass's only
changed entry is the wait.

**What the second pass reads is maintained by the simulation itself**, with
one documented bridge per family: ``install_apt_packages``/``install_packages``
keep the package rows, ``install_apt_repo_file``/``install_repo_file`` the
sources.list bytes and the ``.repo`` file (the same store ``DnfRepoFile``
reads back), the ``SystemctlCommand`` arms the service state and enablement,
and the files mutators the key and config content with full stat rows. The
bridges are what no simulated command can create: the ``listening_ports``/
``tcp_endpoints`` rows the final ``server.wait`` reads (the simulation never
starts a daemon -- the minio precedent) and the canned key bytes behind the
fetch stub; on RedHat also the ``rpm_gpg_keys`` entry's *keyid* -- the
``rpm --import`` stub keys itself by content hash (no OpenPGP parsing), so the
convergence test rewrites the entry to the pinned ``ELASTIC_KEY_ID`` between
passes, which is what a real rpmdb records (D9). The ``0660
root:elasticsearch`` configs are unreadable to the login user, so the second
pass rides ``FileContents``' two-tier elevated read-back (shipped #12) and the
traversal gate (#70), exercised here offline.

The config files need no pre-seed: ``files.install``'s spool-and-install
lands the render *and* the ``mkdir -p`` of the ``jvm.options.d`` parent (D4),
and the content-idempotent write converges on the second pass. The drift
tests do pre-seed them (right content, wrong mode; stale content, right
mode) to drive the two notification paths the companion and the install
each own.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from reemote import run_waves
from reemote.commands import WaitTimeoutError
from reemote.modules.files import files as files_module
from reemote.modules.server import PortInfo, TcpEndpointInfo
from reemote.roles import ElasticsearchVars, elasticsearch
from reemote.roles.elasticsearch import (
    CONF_GROUP,
    CONF_MODE,
    CONF_PATH,
    CONF_USER,
    DEPENDENCIES,
    ELASTIC_KEY_ID,
    HEAP_PATH,
    JVM_LEGACY_PATH,
    KEY_MODE,
    KEY_PATH,
    KEY_URL,
    NOTIFY_RESTART,
    REPO_URL_BASE,
    SERVICE,
    SOURCES_PATH,
    TARGET_FAMILIES,
    WAIT_TIMEOUT,
    ElasticsearchPlan,
    elasticsearch_plan,
    render_elasticsearch_yml,
    render_jvm_options,
    version_major,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from reemote.stages import CheckpointRequest, ControlRequest, FactRequest
from tests.simulated_host import SimulatedHost

#: The handler request keeps ``restart_handler``'s default label, not a name of
#: the role's own -- the notification is read from the notifying request names,
#: the handler logs under the library's generated one.
HANDLER_NAME = "Restart elasticsearch (handler)"

#: The stream's request names in yield order: setup-Debian, the package, the
#: two template install/companion pairs, then the post-checkpoint tail --
#: handler, service task, port wait.
INSTALL_NAMES = (
    "Ensure dependencies are installed",
    "Add Elasticsearch apt key",
    "Add Elasticsearch repository",
    "Install Elasticsearch",
)
CONFIGURE_NAMES = (
    "Configure Elasticsearch",
    "Set Elasticsearch configuration attributes",
    "Configure Elasticsearch JVM options",
    "Set Elasticsearch JVM options attributes",
)
WAIT_NAME = "Wait for Elasticsearch to accept connections"
FIRST_PASS_ORDER = [
    *INSTALL_NAMES,
    *CONFIGURE_NAMES,
    HANDLER_NAME,
    "Start Elasticsearch",
    WAIT_NAME,
]

#: Canned armored-key bytes the ``files.download`` stub returns: valid UTF-8,
#: so the operation's text path preserves them byte-for-byte at the dest.
FAKE_KEY = (
    b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
    b"elasticsearch-canned-armored-key\n"
    b"-----END PGP PUBLIC KEY BLOCK-----\n"
)

#: The repo line the default ``7.x`` channel renders, and the file an
#: unnamed ``apt_repository`` appends to -- sources.list, faithfully (D2).
REPO_LINE = f"deb {REPO_URL_BASE}/7.x/apt stable main"

#: The RedHat install names in yield order: ``setup-RedHat.yml``'s ``rpm_key``
#: and repo-template pair, then the shared ``Install Elasticsearch`` task.
REDHAT_INSTALL_NAMES = (
    "Add Elasticsearch GPG key",
    "Add Elasticsearch repository",
    "Install Elasticsearch",
)

#: ``dnf.repo``'s constructed file for the default ``7.x`` channel (D10): the
#: upstream template's ``[elasticsearch-{{ version }}]`` section id doubles as
#: the filename, and the line order is the operation's fixed construction.
REPO_FILE_PATH = "/etc/yum.repos.d/elasticsearch-7.x.repo"
EXPECTED_REPO_TEXT = (
    "[elasticsearch-7.x]\n"
    "name=Elasticsearch repository for 7.x packages\n"
    f"baseurl={REPO_URL_BASE}/7.x/yum\n"
    "enabled=1\n"
    "gpgcheck=1\n"
    f"gpgkey={KEY_URL}\n"
)

#: ``templates/elasticsearch.yml.j2`` rendered with every default, byte-for-
#: byte: the frozen ``ansible_managed`` header (D3), the interpolated
#: ``localhost``/``9200``, and the blank final lines the empty
#: ``elasticsearch_extra_options`` leaves. Verified against a render of the
#: upstream ``.j2`` through the same environment (so template or environment
#: drift cannot hide).
DEFAULT_YML = """\
# Ansible managed
# ======================== Elasticsearch Configuration =========================
#
# NOTE: Elasticsearch comes with reasonable defaults for most settings.
#       Before you set out to tweak and tune the configuration, make sure you
#       understand what are you trying to accomplish and the consequences.
#
# The primary way of configuring a node is via this file. This template lists
# the most important settings you may want to configure for a production cluster.
#
# Please consult the documentation for further information on configuration options:
# https://www.elastic.co/guide/en/elasticsearch/reference/index.html
#
# ---------------------------------- Cluster -----------------------------------
#
# Use a descriptive name for your cluster:
#
#cluster.name: my-application
#
# ------------------------------------ Node ------------------------------------
#
# Use a descriptive name for the node:
#
#node.name: node-1
#
# Add custom attributes to the node:
#
#node.attr.rack: r1
#
# ----------------------------------- Paths ------------------------------------
#
# Path to directory where to store the data (separate multiple locations by comma):
#
path.data: /var/lib/elasticsearch
#
# Path to log files:
#
path.logs: /var/log/elasticsearch
#
# ----------------------------------- Memory -----------------------------------
#
# Lock the memory on startup:
#
#bootstrap.memory_lock: true
#
# Make sure that the heap size is set to about half the memory available
# on the system and that the owner of the process is allowed to use this
# limit.
#
# Elasticsearch performs poorly when the system is swapping the memory.
#
# ---------------------------------- Network -----------------------------------
#
# Set the bind address to a specific IP (IPv4 or IPv6):
#
network.host: localhost
#
# Set a custom port for HTTP:
#
http.port: 9200
#
# For more information, consult the network module documentation.
#
# --------------------------------- Discovery ----------------------------------
#
# Pass an initial list of hosts to perform discovery when new node is started:
# The default list of hosts is ["127.0.0.1", "[::1]"]
#
#discovery.zen.ping.unicast.hosts: ["host1", "host2"]
#
# Prevent the "split brain" by configuring the majority of nodes (total number of master-eligible nodes / 2 + 1):
#
#discovery.zen.minimum_master_nodes:
#
# For more information, consult the zen discovery module documentation.
#
# ---------------------------------- Gateway -----------------------------------
#
# Block initial recovery after a full cluster restart until N nodes are started:
#
#gateway.recover_after_nodes: 3
#
# For more information, consult the gateway module documentation.
#
# ---------------------------------- Various -----------------------------------
#
# Require explicit names when deleting indices:
#
#action.destructive_requires_name: true


"""

#: ``templates/heap.options.j2`` rendered with the defaults -- the ``>= 7``
#: JVM file. No header comment upstream, so none here; the two heap lines are
#: the whole managed tail.
DEFAULT_HEAP = """\
## JVM configuration

################################################################
## IMPORTANT: JVM heap size
################################################################
##
## You should always set the min and max JVM heap
## size to the same value. For example, to set
## the heap to 4 GB, set:
##
## -Xms4g
## -Xmx4g
##
## See https://www.elastic.co/guide/en/elasticsearch/reference/current/heap-size.html
## for more information
##
################################################################

# Xms represents the initial size of total heap space
# Xmx represents the maximum size of total heap space

-Xms1g
-Xmx2g
"""


@pytest.fixture
def canned_fetch(monkeypatch):
    """Replace the control-side download with a canned-bytes stub.

    ``files.download`` looks ``_fetch_url_bytes`` up as a module global at call
    time, so patching the module attribute redirects the fetch. Returns the
    list of URLs the stub was called with, so a test can assert the fetch
    happened (and that a skipped host and a ``dry`` preview fetch nothing).
    """
    fetched: list[str] = []

    def fake_fetch(url: str) -> bytes:
        fetched.append(url)
        return FAKE_KEY

    monkeypatch.setattr(files_module, "_fetch_url_bytes", fake_fetch)
    return fetched


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one Alpine host that must be skipped.

    The role variables ride in the entry's ``roles`` mapping under
    ``"elasticsearch"``, bound by ``resolve_vars``. With no overrides every
    ``defaults/main.yml`` value applies, so the renders are
    :data:`DEFAULT_YML` and :data:`DEFAULT_HEAP`. The skipped host is Alpine --
    the one family the role has no ``setup-*.yml`` branch for; the RedHat
    target gets its own inventory (``make_redhat_inventory``) so each family's
    global "only the wait changed" assertion stays trivially host-scoped.
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
            "elasticsearch": {
            },
        },
    }
    entry["roles"]["elasticsearch"].update(overrides)
    return [
        entry,
        {
            "host": "web1",
            "family": "Alpine",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "alpine",
        },
    ]


def make_redhat_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A one-host inventory: a single declared-RedHat target.

    The ``setup-RedHat.yml`` twin of :func:`make_inventory`: same bare-field
    role mapping, so an override binds exactly the way an operator writes it,
    and a lone target host keeps the RedHat pass assertions host-global.
    """
    entry: dict[str, Any] = {
        "host": "rocky1",
        "family": "RedHat",
        "connection": {
            "address": "192.0.2.12",
            "username": "supervisor",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "centos",
        "roles": {
            "elasticsearch": {
            },
        },
    }
    entry["roles"]["elasticsearch"].update(overrides)
    return [entry]


def seed(host: SimulatedHost) -> None:
    """Pre-state: a stock minimal Ubuntu machine with no Elasticsearch installed.

    Nothing elastic-related exists yet: no package, no repo line, no key, no
    ``/etc/elasticsearch`` content, and the service is neither running nor
    enabled. Only ``/tmp`` (the spool parent) is pre-existing. The two wait
    seams are seeded because the simulation never starts a daemon: port 9200
    listens for the default ``localhost`` (``Port`` fact) and the example's
    ``127.0.0.1`` (``TcpEndpoint`` fact) is reachable.
    """
    host.apt_packages = {}
    host.apt_sources = []
    host.apt_update_stamp = None
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}
    host.directory_stats = {
        "/tmp": {"mode": 1777, "user": "root", "group": "root"},
    }
    host.existing_files |= set(host.directory_stats)
    host.services = {}
    host.enabled_services = set()
    host.os_groups = ["root", "sudo"]
    host.os_users = {}
    host.listening_ports = {
        ("tcp", 9200): PortInfo(listening=True, process="java", pid=4242)
    }
    host.tcp_endpoints = {
        ("127.0.0.1", 9200): TcpEndpointInfo(reachable=True),
    }


def seed_redhat(host: SimulatedHost) -> None:
    """Pre-state: a stock minimal CentOS machine with no Elasticsearch installed.

    The dnf-side counterpart of :func:`seed`: no package in the
    ``DnfPackages``/``rpm_packages`` stores, no imported key in
    ``rpm_gpg_keys``, no ``.repo`` file or ``/etc/elasticsearch`` content, the
    service neither running nor enabled -- and the same two wait seams, since
    the simulation never starts a daemon (the minio precedent).
    """
    host.packages = {}
    host.rpm_packages = {}
    host.rpm_gpg_keys = []
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}
    host.directory_stats = {
        "/tmp": {"mode": 1777, "user": "root", "group": "root"},
    }
    host.existing_files |= set(host.directory_stats)
    host.services = {}
    host.enabled_services = set()
    host.os_groups = ["root", "sudo"]
    host.os_users = {}
    host.listening_ports = {
        ("tcp", 9200): PortInfo(listening=True, process="java", pid=4242)
    }
    host.tcp_endpoints = {
        ("127.0.0.1", 9200): TcpEndpointInfo(reachable=True),
    }


def make_hosts(inventory: list[dict[str, Any]]) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets.

    Both target families get their stock-machine seed; an Alpine (or otherwise
    off-target) entry keeps its untouched default state, so a role that reached
    it would be caught by the never-touched assertions.
    """
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            seed(host)
        elif entry.get("family") == "RedHat":
            seed_redhat(host)
        hosts[host.name] = host
    return hosts


async def run_role(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SimulatedHost],
    *,
    dry: bool = False,
) -> Any:
    """Resolve variables, build the sync factory's deploy, and run one wave pass."""
    v = resolve_vars(ElasticsearchVars, "elasticsearch", inventory)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        # The *same* host object every pass, so state survives the barriers.
        return hosts[entry["host"]]

    # A plain (non-async) factory: called, not awaited.
    deploy = elasticsearch(v)
    report, _hosts = await run_waves(
        inventory, deploy, host_factory=factory, dry=dry
    )
    return report


def changed_entries(report: Any) -> list[Any]:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def changed_names(report: Any, host: str) -> list[str]:
    """The changed log entry names of one pass on *host*, in log order."""
    return [entry.name for entry in changed_entries(report) if entry.host == host]


def host_log(report: Any, host: str) -> dict[str, Any]:
    """*host*'s log entries of one pass, keyed by name."""
    return {entry.name: entry for entry in report.merged().log if entry.host == host}


def ubuntu_names(report: Any) -> set[str]:
    """Every Ubuntu log entry name of one pass."""
    return {entry.name for entry in report.merged().log if entry.host == "ubuntu"}


def ubuntu_log(report: Any) -> dict[str, Any]:
    """The Ubuntu host's log entries of one pass, keyed by name."""
    return {
        entry.name: entry for entry in report.merged().log if entry.host == "ubuntu"
    }


def _kind(barrier: Any) -> tuple:
    """Classify one barrier record for the shape assertion.

    A ``fact`` barrier names the fact class it reads; a ``checkpoint`` and a
    ``control`` barrier name themselves (the control one through the fetched
    function). An ``act`` barrier would fall through to a distinct label, so a
    speculatively-added barrier breaks the shape test rather than hiding.
    """
    if isinstance(barrier, FactRequest):
        return ("fact", barrier.fact_cls)
    if isinstance(barrier, CheckpointRequest):
        return ("checkpoint", barrier.name)
    if isinstance(barrier, ControlRequest):
        return ("control", barrier.func.__name__)
    return ("other", type(barrier).__name__)


def _barrier_shape(report: Any, host: str = "ubuntu") -> list:
    """*host*'s ordered ``(wave, kind)`` barrier list for one pass."""
    entries = [r for r in report.records if r.host == host]
    return [(r.wave, _kind(r.barrier)) for r in entries if r.barrier is not None]


def _request_waves(report: Any, host: str = "ubuntu") -> set[int]:
    """The set of wave numbers that carried at least one *host* request."""
    return {
        r.wave
        for r in report.records
        if r.host == host and r.log is not None
    }


def _wave_order(report: Any, wave: int, host: str = "ubuntu") -> list[str]:
    """*host*'s request names in one wave, in stream order."""
    return [
        entry.name
        for r in report.records
        if r.host == host and r.wave == wave and r.log is not None
        for entry in ([r.log] if not isinstance(r.log, list) else r.log)
    ]


# --- the stream shape ---------------------------------------------------------


async def test_stream_is_one_checkpoint_and_two_waves(canned_fetch):
    """The Ubuntu stream: the requests, one checkpoint, then the handler tail.

    Exactly one ``checkpoint`` barrier and **no fact/act/control at all** on
    this family -- the plan is entirely static, so anything else would be a
    barrier added speculatively (the RedHat control barrier is the RedHat
    tests' claim). Requests land in two waves (configure 0, tail 1).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert _barrier_shape(report) == [(0, ("checkpoint", "configure done"))]
    assert _request_waves(report) == {0, 1}

    # The rerun re-derives the same barrier -- and keeps *two* request waves,
    # because the service task and the wait follow the checkpoint even when
    # the (absent) handler notified nobody.
    second = await run_role(inventory, hosts)
    assert _barrier_shape(second) == _barrier_shape(report)
    assert _request_waves(second) == {0, 1}


async def test_wave_orders_reproduce_the_task_order(canned_fetch):
    """The role's own order, verbatim: setup, package, templates, flush, tail.

    ``tasks/main.yml`` includes setup-Debian first, then the package, then the
    two ``with_items`` template tasks; ``meta: flush_handlers`` precedes the
    service task and the ``wait_for``. Wave 1 is restart, service, wait.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert _wave_order(report, 0) == [*INSTALL_NAMES, *CONFIGURE_NAMES]
    assert _wave_order(report, 1) == [HANDLER_NAME, "Start Elasticsearch", WAIT_NAME]
    assert [entry.name for entry in report.merged().log if entry.host == "ubuntu"] == (
        FIRST_PASS_ORDER
    )


# --- the first pass really deploys the role -----------------------------------


async def test_first_pass_installs_configures_starts_and_waits(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    # setup-Debian: the two dependencies and the package (the Elastic repo
    # supplies it), with the change-gated refresh having run exactly once.
    assert canned_fetch == [KEY_URL]
    assert set(DEPENDENCIES) <= set(host.apt_packages)
    assert SERVICE in host.apt_packages
    assert host.apt_update_stamp is not None

    # D2: the unnamed apt_repository lands in /etc/apt/sources.list, and the
    # parsed AptSource entry carries the same provenance.
    assert REPO_LINE.encode() in host.remote_files[SOURCES_PATH]
    entry = next(s for s in host.apt_sources if s["filename"] == SOURCES_PATH)
    assert entry["url"] == f"{REPO_URL_BASE}/7.x/apt"
    assert entry["distribution"] == "stable"
    assert entry["components"] == ["main"]

    # The armored key, verbatim through the control-side fetch (D1). The
    # simulation records modes as the octal digits written as an int.
    assert host.remote_files[KEY_PATH] == FAKE_KEY
    assert host.file_stats[KEY_PATH]["mode"] == 644

    # The two rendered configs: root:elasticsearch 0660 (the template tasks'
    # own arguments), full stat rows, and D4's side effect -- the elevated
    # install mkdir -p'ed the drop-in directory, so no directory task exists.
    assert host.remote_files[CONF_PATH] == DEFAULT_YML.encode()
    assert host.file_stats[CONF_PATH] == {
        "user": "root",
        "group": "elasticsearch",
        "mode": 660,
        "size": len(DEFAULT_YML.encode()),
    }
    assert host.remote_files[HEAP_PATH] == DEFAULT_HEAP.encode()
    assert "/etc/elasticsearch/jvm.options.d" in host.directory_stats

    # The service task started and enabled the unit; the wait read the
    # listening-port seam.
    assert host.services["elasticsearch.service"] == "active"
    assert "elasticsearch.service" in host.enabled_services

    # The checkpoint read the config changes, so the handler fired here, and
    # the wait always reports changed (D6).
    log = ubuntu_log(report)
    assert log["Configure Elasticsearch"].changed
    assert log[HANDLER_NAME].changed
    assert log[HANDLER_NAME].host == "ubuntu"
    assert log[WAIT_NAME].changed

    # The whole migration is operations: no shell action ever ran.
    assert host.ran_shells == []


async def test_companions_are_quiet_when_installs_wrote_the_metadata(canned_fetch):
    """``files.install`` writes content *and* metadata, so the companions no-op.

    The companions' purpose is drift on an otherwise-converged file (the
    java/redis/kibana precedent), not a first-pass double write.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    log = ubuntu_log(report)
    assert log["Set Elasticsearch configuration attributes"].changed is False
    assert log["Set Elasticsearch JVM options attributes"].changed is False


async def test_second_pass_only_the_wait_changes(canned_fetch):
    """The migration's idempotence claim, with D6's one honest exception.

    Every *state* request converges -- package present, key content-equal
    (refetched control-side to compare), repo line in place so the
    change-gated refresh never re-runs, both configs byte-identical with the
    right root:elasticsearch 0660 metadata (through the two-tier elevated
    read-back), service running and enabled -- so nobody notifies and the
    handler never appears. Only the stateless port poll still reports
    changed, and it must not drag the restart along with it.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"
    stamp = hosts["ubuntu"].apt_update_stamp

    second = await run_role(inventory, hosts)
    assert [entry.name for entry in changed_entries(second)] == [WAIT_NAME]
    assert HANDLER_NAME not in ubuntu_names(second)
    # The converged pass refetches the key to compare content (the kubectl
    # D3 precedent) but writes nothing and never re-runs apt-get update.
    assert canned_fetch == [KEY_URL, KEY_URL]
    assert hosts["ubuntu"].apt_update_stamp == stamp
    assert hosts["ubuntu"].ran_shells == []


async def test_package_drift_reinstalls_without_notifying(canned_fetch):
    """``Install Elasticsearch`` is not a listener: repair yes, restart no.

    The role carries no ``notify`` on the package task, so a re-install -- a
    changed entry in the checkpoint's wave -- must not fire the handler.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)
    host = hosts["ubuntu"]
    del host.apt_packages[SERVICE]

    second = await run_role(inventory, hosts)
    log = ubuntu_log(second)
    assert log["Install Elasticsearch"].changed
    assert SERVICE in host.apt_packages
    assert HANDLER_NAME not in ubuntu_names(second)
    assert [e.name for e in changed_entries(second)] == ["Install Elasticsearch", WAIT_NAME]


async def test_off_family_host_is_never_touched(canned_fetch):
    """The role has no Alpine branch (upstream ships none): the guard skips.

    The family guard returns before the first yield, so the off-target host is
    in no wave, emits no log line and its stores never move.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    skipped = hosts["web1"]
    assert not any(entry.host == "web1" for entry in report.merged().log)
    assert not any(r.host == "web1" for r in report.records)
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}
    assert skipped.services == {}
    assert skipped.ran_shells == []


async def test_dry_preview_plans_the_requests_and_parks_at_the_checkpoint(canned_fetch):
    """``dry=True`` never runs a barrier, so the tail wave stays unplanned.

    The *Ubuntu* role's first barrier is the checkpoint *after* the configure
    wave, so the dry preview does plan all eight configure requests and stops
    there (the RedHat dry parks earlier, at its opening control barrier).
    Nothing is applied and no notification is reported. The key fetch *does*
    run: dry mode drives the operation generators (their fact gates read the
    host) but never executes a command, and the control-side fetch happens
    before ``files.download`` yields anything -- the kubectl dry test sees no
    fetch only because that role *opens* with a fact barrier, which parks the
    body before its download.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts, dry=True)

    assert canned_fetch == [KEY_URL]
    assert host.apt_packages == {}
    assert host.remote_files == {}
    assert host.file_stats == {}
    assert host.services == {}
    assert changed_entries(report) == []
    assert _barrier_shape(report) == [(0, ("checkpoint", "configure done"))]
    assert _request_waves(report) == set()
    assert report.merged().log == []


async def test_wait_fails_when_nothing_listens(canned_fetch):
    """The wait is a real gate: an unseeded port fails the pass (D6).

    ``server.wait`` polls until its deadline; the simulation evaluates the
    condition once and raises the same ``WaitTimeoutError``, so the role's
    ``timeout=300`` maps to a poll that really consults the ``Port`` fact.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    hosts["ubuntu"].listening_ports = {}

    with pytest.raises(WaitTimeoutError, match="'listening': False"):
        await run_role(inventory, hosts)


# --- the RedHat branch ----------------------------------------------------------


async def test_redhat_stream_leads_with_one_control_barrier(canned_fetch):
    """setup-RedHat.yml's shape: the key fetch closes an empty wave 0.

    The ``control(files.fetch_url)`` barrier is the family's only addition to
    the barrier surface (the repo_epel shape): wave 1 then carries the dnf
    setup and the shared configure requests up to the checkpoint, and wave 2
    is the same handler/service/wait tail. The rerun re-derives the same
    barrier sequence -- the stream is static, only the change flags converge.
    """
    inventory = make_redhat_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert _barrier_shape(report, "rocky1") == [
        (0, ("control", "fetch_url")),
        (1, ("checkpoint", "configure done")),
    ]
    assert _request_waves(report, "rocky1") == {1, 2}
    assert _wave_order(report, 1, "rocky1") == [
        *REDHAT_INSTALL_NAMES,
        *CONFIGURE_NAMES,
    ]
    assert _wave_order(report, 2, "rocky1") == [
        HANDLER_NAME,
        "Start Elasticsearch",
        WAIT_NAME,
    ]

    second = await run_role(inventory, hosts)
    assert _barrier_shape(second, "rocky1") == _barrier_shape(report, "rocky1")


async def test_redhat_first_pass_imports_key_adds_repo_installs(canned_fetch):
    """The setup tasks end to end: ``dnf.key``, the constructed ``dnf.repo``,
    ``dnf.packages``, then the family-neutral config/service/wait tail.

    The armored key arrives through the control barrier and the import is
    pinned to ``ELASTIC_KEY_ID`` (D9); the repo file is ``dnf.repo``'s
    construction of the upstream template's still-meaningful lines (D10).
    Nothing apt-side moves and no shell action ever runs.
    """
    inventory = make_redhat_inventory()
    hosts = make_hosts(inventory)
    host = hosts["rocky1"]
    report = await run_role(inventory, hosts)

    assert canned_fetch == [KEY_URL]
    # ``rpm --import`` ran on the fetched bytes and left one rpmdb entry --
    # stub-keyed by content hash, bridged to the real id in the rerun (D9).
    assert host.imported_keys == {FAKE_KEY}
    assert len(host.rpm_gpg_keys) == 1

    # The constructed repo file (D10): the section-id filename, the template's
    # lines in the operation's fixed order, mode 0644, and no cache-refresh
    # request anywhere -- upstream's RedHat path ran none either.
    assert host.remote_files[REPO_FILE_PATH] == EXPECTED_REPO_TEXT.encode()
    assert host.file_stats[REPO_FILE_PATH]["mode"] == 644

    # The package landed through dnf, not apt.
    assert "elasticsearch" in host.packages
    assert host.apt_packages == {}
    assert host.apt_sources == []

    # The shared tail: both rendered configs, the started/enabled unit.
    assert host.remote_files[CONF_PATH] == DEFAULT_YML.encode()
    assert host.remote_files[HEAP_PATH] == DEFAULT_HEAP.encode()
    assert host.services["elasticsearch.service"] == "active"
    assert "elasticsearch.service" in host.enabled_services

    log = host_log(report, "rocky1")
    assert log["Add Elasticsearch GPG key"].changed
    assert log["Add Elasticsearch repository"].changed
    assert log["Configure Elasticsearch"].changed
    assert log[HANDLER_NAME].changed
    assert log[WAIT_NAME].changed
    assert host.ran_shells == []


async def test_redhat_second_pass_only_the_wait_changes(canned_fetch):
    """The RedHat convergence claim, across the one documented simulation bridge.

    The ``rpm --import`` stub records a content-hash keyid no OpenPGP tool
    would name, so the test rewrites the entry to the pinned id -- what a
    real rpmdb holds -- before the rerun. With that, ``dnf.key``
    short-circuits through ``RpmGpgKeys``, the repo file and package are
    state-equal, the configs converge, nobody notifies, and only the
    stateless wait still reports changed (D6). The control fetch runs every
    pass -- it feeds the import's gate; the converged import writes nothing.
    """
    inventory = make_redhat_inventory()
    hosts = make_hosts(inventory)
    host = hosts["rocky1"]
    first = await run_role(inventory, hosts)
    assert changed_names(first, "rocky1")

    for entry in host.rpm_gpg_keys:  # the keyid bridge (D9's simulation limit)
        entry["keyid"] = ELASTIC_KEY_ID

    second = await run_role(inventory, hosts)
    assert changed_names(second, "rocky1") == [WAIT_NAME]
    assert HANDLER_NAME not in host_log(second, "rocky1")
    assert canned_fetch == [KEY_URL, KEY_URL]
    assert host.ran_shells == []


async def test_redhat_repo_drift_readds_without_notifying(canned_fetch):
    """A removed ``.repo`` is rewritten by the next pass -- and no restart.

    ``dnf.repo``'s fact gate reads the destination back, so deleting the file
    (content, existence and stat seams together) re-plans exactly the repo
    request; its name is not a listener, so the handler stays away.
    """
    inventory = make_redhat_inventory()
    hosts = make_hosts(inventory)
    host = hosts["rocky1"]
    await run_role(inventory, hosts)
    del host.remote_files[REPO_FILE_PATH]
    host.existing_files.discard(REPO_FILE_PATH)
    host.file_stats.pop(REPO_FILE_PATH, None)
    for entry in host.rpm_gpg_keys:
        entry["keyid"] = ELASTIC_KEY_ID

    second = await run_role(inventory, hosts)
    log = host_log(second, "rocky1")
    assert log["Add Elasticsearch repository"].changed
    assert host.remote_files[REPO_FILE_PATH] == EXPECTED_REPO_TEXT.encode()
    assert HANDLER_NAME not in log
    assert changed_names(second, "rocky1") == ["Add Elasticsearch repository", WAIT_NAME]


async def test_redhat_dry_preview_parks_at_the_control_barrier(canned_fetch):
    """The RedHat dry fetches nothing: the body stops *before* any request.

    Unlike the Ubuntu dry -- where the fetch rides the planned
    ``files.download`` because it lives inside the operation -- the RedHat
    barrier *is* the fetch, and ``dry=True`` never runs a barrier: no log, no
    planned request, no fetch, no store mutation.
    """
    inventory = make_redhat_inventory()
    hosts = make_hosts(inventory)
    host = hosts["rocky1"]
    report = await run_role(inventory, hosts, dry=True)

    assert canned_fetch == []
    assert _barrier_shape(report, "rocky1") == [(0, ("control", "fetch_url"))]
    assert _request_waves(report, "rocky1") == set()
    assert report.merged().log == []
    assert changed_entries(report) == []
    assert host.packages == {}
    assert host.rpm_gpg_keys == []
    assert host.remote_files == {}
    assert host.services == {}


# --- the version branch ---------------------------------------------------------


async def test_legacy_version_manages_the_full_jvm_options_file(canned_fetch):
    """``version: 6.8.23``: the ``< 7`` branch (major ``6``) writes jvm.options.

    ``tasks/main.yml``'s two ``when=``-gated template tasks: below 7 the whole
    ``jvm.options`` (expert settings included) is managed, not the
    ``jvm.options.d`` drop-in -- and the repo channel follows the version.
    """
    inventory = make_inventory(version="6.8.23")
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    await run_role(inventory, hosts)

    jvm = host.remote_files[JVM_LEGACY_PATH].decode("utf-8")
    assert "-Xms1g\n-Xmx2g\n" in jvm
    assert "8-13:-XX:+UseConcMarkSweepGC" in jvm
    assert "10-:-XX:UseAVX=2" in jvm
    assert HEAP_PATH not in host.remote_files
    assert "/etc/elasticsearch/jvm.options.d" not in host.directory_stats
    assert f"{REPO_URL_BASE}/6.8.23/apt".encode() in host.remote_files[SOURCES_PATH]

    # The branch converges the same way: only the stateless wait reports.
    second = await run_role(inventory, hosts)
    assert [entry.name for entry in changed_entries(second)] == [WAIT_NAME]


async def test_ten_x_keeps_the_first_character_quirk():
    """``[0] | int`` read ``"10.x"`` as major 1 -- so the plan still does (D5).

    The role's Jinja expression indexed the *first character*, meaning a
    two-digit major silently took the ``< 7`` branch. The migration keeps the
    quirk rather than "fixing" it into a different deploy than the role ran.
    """
    assert version_major("10.x") == 1
    plan = elasticsearch_plan(ElasticsearchVars(version="10.x"))
    assert plan.jvm_path == JVM_LEGACY_PATH


# --- the override branches ------------------------------------------------------


async def test_example_overrides_ride_the_inventory(canned_fetch):
    """The quickstart's override pair, end to end.

    ``network_host: 127.0.0.1`` rewrites the rendered ``network.host`` *and*
    moves the wait from the ``Port`` fact to the ``TcpEndpoint`` seam (the
    issue-#56 ``host=`` variant); ``heap_size_min: 2g`` lifts the ``-Xms``
    line in the heap drop-in.
    """
    inventory = make_inventory(network_host="127.0.0.1", heap_size_min="2g")
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    assert b"network.host: 127.0.0.1\n" in host.remote_files[CONF_PATH]
    assert b"-Xms2g\n-Xmx2g\n" in host.remote_files[HEAP_PATH]
    # The TcpEndpoint-seeded wait ran (and reported changed, as ever).
    assert ubuntu_log(report)[WAIT_NAME].changed

    second = await run_role(inventory, hosts)
    assert [entry.name for entry in changed_entries(second)] == [WAIT_NAME]


async def test_extra_options_append_verbatim(canned_fetch):
    """The template's final ``{{ elasticsearch_extra_options }}`` substitution."""
    inventory = make_inventory(extra_options="cluster.name: reemote\n")
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    await run_role(inventory, hosts)

    config = host.remote_files[CONF_PATH].decode("utf-8")
    # The value carried its own newline, so the interpolated line keeps a
    # blank final line after it.
    assert config.endswith("#action.destructive_requires_name: true\n\ncluster.name: reemote\n\n")

    second = await run_role(inventory, hosts)
    assert [entry.name for entry in changed_entries(second)] == [WAIT_NAME]


async def test_stopped_service_state_powers_the_unit_off(canned_fetch):
    """``service_state: stopped``: ``running=False`` on the service task.

    The state axis maps straight through; the wait still passes because the
    simulation's port seam is static, and enablement stays on its own axis.
    """
    inventory = make_inventory(service_state="stopped")
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    await run_role(inventory, hosts)

    assert host.services["elasticsearch.service"] == "inactive"
    assert "elasticsearch.service" in host.enabled_services

    second = await run_role(inventory, hosts)
    assert [entry.name for entry in changed_entries(second)] == [WAIT_NAME]


# --- the notification channel ---------------------------------------------------


def test_only_the_config_requests_notify():
    """The setup, package, service and wait names are deliberately not listeners.

    The role's one ``notify:`` sits on both version-gated template tasks; the
    two metadata companions join them for owner/group/mode drift (D4). The
    wait's absence is load-bearing: it always reports changed, so admitting
    it would restart the service forever (D6).
    """
    assert NOTIFY_RESTART == frozenset(set(CONFIGURE_NAMES))
    for name in (*INSTALL_NAMES, "Start Elasticsearch", WAIT_NAME):
        assert name not in NOTIFY_RESTART


def test_frozen_constants_are_the_role_files():
    """``defaults/main.yml`` + both ``setup-*.yml`` files, collapsed to constants."""
    assert TARGET_FAMILIES == ("Ubuntu", "RedHat")
    assert DEPENDENCIES == ("apt-transport-https", "gnupg2")
    assert KEY_URL == "https://artifacts.elastic.co/GPG-KEY-elasticsearch"
    assert ELASTIC_KEY_ID == "d88e42b4"
    assert KEY_PATH == "/etc/apt/trusted.gpg.d/elasticsearch.asc"
    assert KEY_MODE == "0644"
    assert REPO_URL_BASE == "https://artifacts.elastic.co/packages"
    assert SOURCES_PATH == "/etc/apt/sources.list"
    assert SERVICE == "elasticsearch"
    assert CONF_PATH == "/etc/elasticsearch/elasticsearch.yml"
    assert JVM_LEGACY_PATH == "/etc/elasticsearch/jvm.options"
    assert HEAP_PATH == "/etc/elasticsearch/jvm.options.d/heap.options"
    assert CONF_MODE == "0660"
    assert CONF_USER == "root"
    assert CONF_GROUP == "elasticsearch"
    assert WAIT_TIMEOUT == 300.0


# --- the pure helpers, unit-tested directly --------------------------------------


def test_render_matches_the_template_defaults():
    """The Jinja2 renders are the template sources with every default filled."""
    yml = render_elasticsearch_yml(ElasticsearchVars())
    assert yml == DEFAULT_YML
    assert yml.endswith("#action.destructive_requires_name: true\n\n\n")

    heap = render_jvm_options(ElasticsearchVars())
    assert heap == DEFAULT_HEAP
    assert not heap.startswith("# Ansible managed")


def test_render_jvm_branch():
    """The ``< 7`` render is the expert-settings template, ``>= 7`` the drop-in."""
    legacy = render_jvm_options(ElasticsearchVars(version="6.x"))
    assert "8-13:-XX:+UseConcMarkSweepGC" in legacy
    assert legacy.endswith("-Xms1g\n-Xmx2g\n") or "-Xms1g\n-Xmx2g\n" in legacy

    modern = render_jvm_options(ElasticsearchVars(version="8.x"))
    assert modern == DEFAULT_HEAP


def test_version_major_is_the_first_character():
    """``elasticsearch_version[0] | int`` to the letter (D5)."""
    assert version_major("7.x") == 7
    assert version_major("6.8.23") == 6
    assert version_major("10.x") == 1


def test_elasticsearch_plan_freezes_the_render_and_vars():
    """The plan is the renders, the version-selected path and the repo line."""
    plan = elasticsearch_plan(
        ElasticsearchVars(version="6.x", package="elasticsearch-oss", http_port=9201)
    )
    assert isinstance(plan, ElasticsearchPlan)
    assert plan.config_content == render_elasticsearch_yml(
        ElasticsearchVars(version="6.x", http_port=9201)
    )
    assert plan.jvm_path == JVM_LEGACY_PATH
    assert plan.jvm_content == render_jvm_options(ElasticsearchVars(version="6.x"))
    assert plan.package == "elasticsearch-oss"
    assert plan.repo_src == f"deb {REPO_URL_BASE}/6.x/apt stable main"
    assert plan.repo_section == "elasticsearch-6.x"
    assert plan.repo_baseurl == f"{REPO_URL_BASE}/6.x/yum"
    assert plan.repo_description == "Elasticsearch repository for 6.x packages"
    assert hash(plan) == hash(elasticsearch_plan(ElasticsearchVars(version="6.x", package="elasticsearch-oss", http_port=9201)))


def test_model_is_frozen_and_hashable():
    v = ElasticsearchVars()
    assert hash(v) is not None
    with pytest.raises(ValidationError):
        v.http_port = 9201


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"version": "x.y"}, "version"),
        ({"version": ""}, "version"),
        ({"version": "v7.x"}, "version"),
        ({"package": ""}, "package"),
        ({"network_host": ""}, "network_host"),
        ({"heap_size_min": ""}, "heap_size_min"),
        ({"heap_size_max": ""}, "heap_size_max"),
        ({"http_port": "not-a-number"}, "http_port"),
        ({"package_state": "absent"}, "package_state"),
        ({"service_state": "enabled"}, "service_state"),
    ],
)
def test_invalid_role_values_fail_fast(overrides, match):
    """The role's implicit gates are pydantic rejections now (D5).

    ``"x.y"`` is the D5 case: Ansible's ``| int`` coerced it to ``0`` and
    silently took the ``< 7`` branch; ``resolve_vars`` refuses it instead.
    """
    inventory = make_inventory(**overrides)
    with pytest.raises(ValidationError, match=match):
        resolve_vars(ElasticsearchVars, "elasticsearch", inventory)


def test_numeric_strings_coerce_into_the_integer_fields():
    """The one widening over strict types: pydantic's lax int coercion.

    ``"9201"`` binds to ``http_port`` as ``9201`` (the repo-wide convention,
    as in ``RedisVars.port``) and renders identically.
    """
    v = resolve_vars(ElasticsearchVars, "elasticsearch", make_inventory(http_port="9201"))
    assert v["ubuntu"].http_port == 9201
    assert "http.port: 9201\n" in render_elasticsearch_yml(v["ubuntu"])


def test_inventory_role_variables_bind_and_typos_raise():
    """The nested mapping binds bare names; a typo raises RoleVarsError."""
    inventory = make_inventory(
        version="6.x", extra_options="node.name: node-1", service_enabled=False
    )
    v = resolve_vars(ElasticsearchVars, "elasticsearch", inventory)
    assert v["ubuntu"].version == "6.x"
    assert v["ubuntu"].extra_options == "node.name: node-1"
    assert v["ubuntu"].service_enabled is False

    with pytest.raises(RoleVarsError, match="packagez"):
        resolve_vars(ElasticsearchVars, "elasticsearch", make_inventory(packagez="x"))
