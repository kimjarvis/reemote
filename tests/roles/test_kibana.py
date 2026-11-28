# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``kibana`` role (§17.5).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, kibana(v), host_factory=...)``. The factory is plain
(non-``async``) -- every input is a resolved variable or a frozen constant --
so it is called, not awaited. The control-side key fetch is stubbed at
``files._fetch_url_bytes`` (autouse-style fixture), so nothing touches the
network.

The load-bearing assertion is the migration's shape claim: the stream is
**two waves** and the checkpoint is the **only barrier** -- the seven configure
requests, then ``checkpoint("configure done")`` closing that wave, then the
handler wave for the notified hosts. No ``fact``, no ``act``, no ``control``:
the ``get_url``/``apt_repository`` pair are operations whose own fact gates
live inside their commands, so a speculative barrier breaks the shape test,
which reads the records straight out of ``report.records``. On a converged
rerun nothing notifies, so the handler wave never appears at all (one wave,
not two) and the repository's change-gated refresh leaves its ``apt-get
update`` stamp alone.

**No between-pass bridge is needed**: the simulation's own mutators maintain
every seam the second pass reads -- ``install_apt_packages`` records the
dependencies and the package, the spool-install ``sh -c`` the key and config
content and stat rows, ``apt.repo`` the source line and update stamp, the
``SystemctlCommand`` arms the service state and enablement. Everything is
login-readable ``0644``, so unlike the redis role's hidden ``0640`` config no
elevated read-back is involved.

The drift tests exercise the three notification paths: content drift (the
install rewrites and notifies), metadata drift (the ``files.file`` companion
converges and notifies, D3), and key drift (the download rewrites but its
name carries no ``notify``, so nobody restarts).
"""

import pytest
from pydantic import ValidationError

from reemote import run_waves
from reemote.modules.files import files as files_module
from reemote.roles import KibanaVars, kibana
from reemote.roles.kibana import (
    BUNDLED_TEMPLATE,
    DEPENDENCIES,
    KEY_MODE,
    KEY_PATH,
    KEY_URL,
    NOTIFY_RESTART,
    REPO_URL_BASE,
    SERVICE,
    KibanaPlan,
    kibana_plan,
    render_kibana_conf,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from reemote.stages import CheckpointRequest, FactRequest
from tests.simulated_host import SimulatedHost

#: The handler request keeps ``restart_handler``'s default label, not a name of
#: the role's own -- the notification is read from the notifying request names,
#: the handler logs under the library's generated one.
HANDLER_NAME = "Restart kibana (handler)"

#: The path the role installs the rendered config at (``kibana_config_file_path``).
CONF_PATH = "/etc/kibana/kibana.yml"

#: An armored OpenPGP-shaped body: valid UTF-8, so ``files.download`` takes the
#: text install path exactly as the real armored ``elasticsearch.asc`` does.
FAKE_KEY = b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n\nmQIN fake elastic key\n=ABCD\n-----END PGP PUBLIC KEY BLOCK-----\n"

#: The role template rendered with every default, byte-for-byte: the two
#: ``{% if %}`` guards resolved to their false branches (``elasticsearch.hosts``
#: on the ``7.x`` channel, the commented-out auth pair), captured from a real
#: render through :func:`reemote.roles._render.jinja2_environment` (so template
#: or environment drift cannot hide).
DEFAULT_CONFIG = """\
# Kibana is served by a back end server. This setting specifies the port to use.
server.port: 5601

# Specifies the address to which the Kibana server will bind. IP addresses and host names are both valid values.
# The default is 'localhost', which usually means remote machines will not be able to connect.
# To allow connections from remote users, set this parameter to a non-loopback address.
server.host: "0.0.0.0"

# Enables you to specify a path to mount Kibana at if you are running behind a proxy.
# Use the `server.rewriteBasePath` setting to tell Kibana if it should remove the basePath
# from requests it receives, and to prevent a deprecation warning at startup.
# This setting cannot end in a slash.
#server.basePath: ""

# Specifies whether Kibana should rewrite requests that are prefixed with
# `server.basePath` or require that they are rewritten by your reverse proxy.
# This setting was effectively always `false` before Kibana 6.3 and will
# default to `true` starting in Kibana 7.0.
#server.rewriteBasePath: false

# The maximum payload size in bytes for incoming server requests.
#server.maxPayloadBytes: 1048576

# The Kibana server's name.  This is used for display purposes.
#server.name: "your-hostname"

# The URL of the Elasticsearch instance to use for all your queries.
elasticsearch.hosts: http://localhost:9200

# When this setting's value is true Kibana uses the hostname specified in the server.host
# setting. When the value of this setting is false, Kibana uses the hostname of the host
# that connects to this Kibana instance.
#elasticsearch.preserveHost: true

# Kibana uses an index in Elasticsearch to store saved searches, visualizations and
# dashboards. Kibana creates a new index if the index doesn't already exist.
#kibana.index: ".kibana"

# The default application to load.
#kibana.defaultAppId: "home"

# If your Elasticsearch is protected with basic authentication, these settings provide
# the username and password that the Kibana server uses to perform maintenance on the Kibana
# index at startup. Your Kibana users still need to authenticate with Elasticsearch, which
# is proxied through the Kibana server.
#elasticsearch.username: "user"
#elasticsearch.password: "pass"

# Enables SSL and paths to the PEM-format SSL certificate and SSL key files, respectively.
# These settings enable SSL for outgoing requests from the Kibana server to the browser.
#server.ssl.enabled: false
#server.ssl.certificate: /path/to/your/server.crt
#server.ssl.key: /path/to/your/server.key

# Optional settings that provide the paths to the PEM-format SSL certificate and key files.
# These files validate that your Elasticsearch backend uses the same key files.
#elasticsearch.ssl.certificate: /path/to/your/client.crt
#elasticsearch.ssl.key: /path/to/your/client.key

# Optional setting that enables you to specify a path to the PEM file for the certificate
# authority for your Elasticsearch instance.
#elasticsearch.ssl.certificateAuthorities: [ "/path/to/your/CA.pem" ]

# To disregard the validity of SSL certificates, change this setting's value to 'none'.
#elasticsearch.ssl.verificationMode: full

# Time in milliseconds to wait for Elasticsearch to respond to pings. Defaults to the value of
# the elasticsearch.requestTimeout setting.
#elasticsearch.pingTimeout: 1500

# Time in milliseconds to wait for responses from the back end or Elasticsearch. This value
# must be a positive integer.
#elasticsearch.requestTimeout: 30000

# List of Kibana client-side headers to send to Elasticsearch. To send *no* client-side
# headers, set this value to [] (an empty list).
#elasticsearch.requestHeadersWhitelist: [ authorization ]

# Header names and values that are sent to Elasticsearch. Any custom headers cannot be overwritten
# by client-side headers, regardless of the elasticsearch.requestHeadersWhitelist configuration.
#elasticsearch.customHeaders: {}

# Time in milliseconds for Elasticsearch to wait for responses from shards. Set to 0 to disable.
#elasticsearch.shardTimeout: 30000

# Time in milliseconds to wait for Elasticsearch at Kibana startup before retrying.
#elasticsearch.startupTimeout: 5000

# Logs queries sent to Elasticsearch. Requires logging.verbose set to true.
#elasticsearch.logQueries: false

# Specifies the path where Kibana creates the process ID file.
#pid.file: /var/run/kibana.pid

# Enables you specify a file where Kibana stores log output.
#logging.dest: stdout

# Set the value of this setting to true to suppress all logging output.
#logging.silent: false

# Set the value of this setting to true to suppress all logging output other than error messages.
#logging.quiet: false

# Set the value of this setting to true to log all events, including system usage information
# and all requests.
#logging.verbose: false

# Set the interval in milliseconds to sample system and process performance
# metrics. Minimum is 100ms. Defaults to 5000.
#ops.interval: 5000

# The default locale. This locale can be used in certain circumstances to substitute any missing
# translations.
#i18n.defaultLocale: "en"
"""


@pytest.fixture
def canned_fetch(monkeypatch):
    """Replace the control-side key fetch with a canned-bytes stub.

    ``files.download`` looks ``_fetch_url_bytes`` up as a module global at call
    time, so patching the module attribute redirects the fetch. Returns the list
    of URLs the stub was called with, so a test can assert the fetch happened
    (and that a skipped host fetched nothing).
    """
    fetched: list[str] = []

    def fake_fetch(url: str) -> bytes:
        fetched.append(url)
        return FAKE_KEY

    monkeypatch.setattr(files_module, "_fetch_url_bytes", fake_fetch)
    return fetched


def make_inventory(**overrides) -> list[dict]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride in the entry's ``roles`` mapping under ``"kibana"``,
    bound by ``resolve_vars``. With no overrides every ``defaults/main.yml``
    value applies, so the render is :data:`DEFAULT_CONFIG`.
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
        "roles": {
            "kibana": {
            },
        },
    }
    entry["roles"]["kibana"].update(overrides)
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
    """Pre-state: a stock minimal Ubuntu machine with no Kibana installed.

    Nothing kibana-related exists yet: no packages, no repository line, no key
    file, no config, and the service is neither running nor enabled. The apt
    update stamp is zeroed so the repository's change-gated refresh is visible
    on the first pass (and provably untouched on the second).
    """
    host.apt_packages = {}
    host.apt_sources = []
    host.apt_update_stamp = None
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}
    host.directory_stats = {}
    host.services = {}
    host.enabled_services = set()
    host.os_groups = ["root", "sudo"]
    host.os_users = {}


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
    v = resolve_vars(KibanaVars, "kibana", inventory)

    def factory(entry: dict) -> SimulatedHost:
        # The *same* host object every pass, so state survives the barriers.
        return hosts[entry["host"]]

    # A plain (non-async) factory: called, not awaited.
    deploy = kibana(v)
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


async def test_stream_is_one_checkpoint_and_two_waves(canned_fetch):
    """The role's shape: the requests, one checkpoint, then the handler wave.

    Exactly one ``checkpoint`` barrier and **no fact/act/control at all** -- the
    plan is entirely static, the operations own their fact gates, so anything
    else would be a barrier added speculatively. Requests land in two waves
    (configure 0, handler 1).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert _barrier_shape(report) == [(0, ("checkpoint", "configure done"))]
    assert _request_waves(report) == {0, 1}

    # The rerun re-derives the same barrier; nothing notifies, so no second
    # request wave appears at all.
    second = await run_role(inventory, hosts)
    assert _barrier_shape(second) == _barrier_shape(report)
    assert _request_waves(second) == {0}


async def test_request_order_matches_the_role(canned_fetch):
    """``setup-Debian.yml`` before ``main.yml``, each in its file's order.

    dependencies, key, repository, package, service, config, companion -- then
    the checkpoint-selected handler under the library's default label.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    wave0 = [entry.name for entry in report.merged().log if entry.host == "ubuntu"]
    assert wave0 == [
        "Ensure dependencies are installed",
        "Add Elasticsearch apt key",
        "Add Kibana repository",
        "Install Kibana",
        "Ensure Kibana is started and enabled at boot",
        "Copy Kibana configuration",
        "Set Kibana configuration attributes",
        HANDLER_NAME,
    ]


# --- the first pass really deploys the role -----------------------------------


async def test_first_pass_installs_configures_and_notifies(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    # The two dependency packages and the role package (recipe-pinned names).
    assert set(DEPENDENCIES) <= set(host.apt_packages)
    assert "kibana" in host.apt_packages

    # The key: the armored bytes verbatim in trusted.gpg.d, login-readable
    # 0644 (D1), fetched control-side from the role's URL.
    assert canned_fetch == [KEY_URL]
    assert host.remote_files[KEY_PATH] == FAKE_KEY
    assert host.file_stats[KEY_PATH] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(FAKE_KEY),
    }

    # The repository: the version-selected line in the explicit drop-in (D2),
    # and because the line was new, the gated refresh ran.
    assert host.apt_sources == [
        {
            "type": "deb",
            "url": f"{REPO_URL_BASE}/7.x/apt",
            "distribution": "stable",
            "components": ["main"],
            "options": {},
            "filename": "/etc/apt/sources.list.d/kibana-7.x.list",
        }
    ]
    assert host.apt_update_stamp is not None

    # The service is started and enabled (main.yml's service task).
    assert host.services["kibana.service"] == "active"
    assert "kibana.service" in host.enabled_services

    # The config: the default render, root:root 0644, full stat row.
    assert host.remote_files[CONF_PATH] == DEFAULT_CONFIG.encode()
    assert host.file_stats[CONF_PATH] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(DEFAULT_CONFIG.encode()),
    }

    # The checkpoint read the config change, so the handler fired on this host.
    log = ubuntu_log(report)
    assert log["Copy Kibana configuration"].changed
    assert log[HANDLER_NAME].changed
    assert log[HANDLER_NAME].host == "ubuntu"

    # The whole migration is operations: no shell action ever ran.
    assert host.ran_shells == []


async def test_companion_is_quiet_when_install_wrote_the_metadata(canned_fetch):
    """``files.install`` writes content *and* metadata, so the companion no-ops.

    The companion's purpose is drift on an otherwise-converged file (the
    java/redis precedent), not a first-pass double write.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert ubuntu_log(report)["Set Kibana configuration attributes"].changed is False


async def test_second_pass_is_a_noop_with_no_handler_wave(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"
    stamp = hosts["ubuntu"].apt_update_stamp

    # The migration's idempotence claim: nothing changed anywhere and no handler
    # ran -- packages present, key content-equal, repository line in place (so
    # the change-gated refresh never ran again), config byte-identical with the
    # right metadata, service running and enabled, so nobody notified.
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert HANDLER_NAME not in ubuntu_names(second)
    assert hosts["ubuntu"].apt_update_stamp == stamp
    assert hosts["ubuntu"].ran_shells == []


async def test_non_ubuntu_host_is_never_touched(canned_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    skipped = hosts["web1"]

    # The family guard returns before the first yield, so the off-target host is
    # in no wave, emits no log line, its stores never move and the key was
    # fetched for the Ubuntu host alone.
    assert not any(entry.host == "web1" for entry in report.merged().log)
    assert not any(r.host == "web1" for r in report.records)
    assert canned_fetch == [KEY_URL]
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}
    assert skipped.services == {}
    assert skipped.ran_shells == []


async def test_dry_preview_plans_the_requests_and_parks_at_the_checkpoint(canned_fetch):
    """``dry=True`` never runs a barrier, so the handler wave stays unplanned.

    The role's first barrier is the checkpoint *after* the request wave, so the
    dry preview does plan all seven configure requests (the request wave needs
    one control-side key fetch to compute the content comparison) and stops
    there. Nothing is applied and no notification is reported.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts, dry=True)

    assert host.apt_packages == {}
    assert host.apt_sources == []
    assert host.remote_files == {}
    assert host.services == {}
    assert canned_fetch == [KEY_URL]
    assert changed_entries(report) == []
    # The planned request records carry no apply log; one barrier record, at
    # wave 0, and no report for it to read.
    assert _barrier_shape(report) == [(0, ("checkpoint", "configure done"))]
    assert _request_waves(report) == set()
    assert report.merged().log == []


# --- the notification paths ----------------------------------------------------


async def test_metadata_drift_converges_and_notifies(canned_fetch):
    """Right content, wrong mode: the companion converges it and queues the restart.

    ``files.install`` is content-idempotent, so the install request stays quiet;
    the paired ``files.file`` reports the mode change -- and its name notifies,
    because Ansible's template reports a change on owner/group/mode drift too
    (D3).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    host.remote_files[CONF_PATH] = DEFAULT_CONFIG.encode()
    host.existing_files.add(CONF_PATH)
    host.file_stats[CONF_PATH] = {
        "user": "root",
        "group": "kibana",
        "mode": 600,
        "size": len(DEFAULT_CONFIG.encode()),
    }

    report = await run_role(inventory, hosts)
    log = ubuntu_log(report)

    assert log["Copy Kibana configuration"].changed is False
    assert log["Set Kibana configuration attributes"].changed
    assert log[HANDLER_NAME].changed
    assert host.file_stats[CONF_PATH] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(DEFAULT_CONFIG.encode()),
    }

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert HANDLER_NAME not in ubuntu_names(second)


async def test_content_drift_reinstalls_and_notifies(canned_fetch):
    """Stale content: the install request rewrites the file and queues the restart."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    host.remote_files[CONF_PATH] = b"# stale config\n"
    host.existing_files.add(CONF_PATH)
    host.file_stats[CONF_PATH] = {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(b"# stale config\n"),
    }

    report = await run_role(inventory, hosts)
    log = ubuntu_log(report)

    assert log["Copy Kibana configuration"].changed
    assert log[HANDLER_NAME].changed
    assert host.remote_files[CONF_PATH] == DEFAULT_CONFIG.encode()

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_key_drift_reinstalls_but_notifies_nobody(canned_fetch):
    """The key task carries no ``notify`` in the role: a rewrite must not restart.

    Converge once, then replace the key's bytes. The download rewrites them --
    and only them -- so the wave has a changed entry the checkpoint must skip.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    await run_role(inventory, hosts)

    host.remote_files[KEY_PATH] = b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nstale\n"
    host.file_stats[KEY_PATH]["size"] = len(host.remote_files[KEY_PATH])

    report = await run_role(inventory, hosts)
    log = ubuntu_log(report)

    assert log["Add Elasticsearch apt key"].changed
    assert host.remote_files[KEY_PATH] == FAKE_KEY
    assert HANDLER_NAME not in ubuntu_names(report)

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the override branches ------------------------------------------------------


async def test_version_6x_switches_repo_and_template_key(canned_fetch):
    """One variable drives both branches, exactly as in the role.

    ``version="6.x"`` selects the ``6.x`` repository line *and* the template's
    true branch, ``elasticsearch.url`` instead of ``elasticsearch.hosts``.
    """
    inventory = make_inventory(version="6.x")
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    await run_role(inventory, hosts)

    assert host.apt_sources[0]["url"] == f"{REPO_URL_BASE}/6.x/apt"
    assert (
        host.apt_sources[0]["filename"] == "/etc/apt/sources.list.d/kibana-6.x.list"
    )
    written = host.remote_files[CONF_PATH]
    assert b"\nelasticsearch.url: http://localhost:9200\n" in written
    assert b"elasticsearch.hosts" not in written

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_enabled_false_leaves_the_unit_disabled_but_running(canned_fetch):
    """``kibana_service_enabled: false`` maps to ``enabled=False`` on the service task."""
    inventory = make_inventory(enabled=False)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    await run_role(inventory, hosts)

    assert host.services["kibana.service"] == "active"
    assert "kibana.service" not in host.enabled_services

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_custom_config_template_is_read_controller_side(canned_fetch, tmp_path):
    """The swap knob in D4: a non-sentinel value is a controller-side path.

    The file is read and rendered on the controller with the same context, so
    a two-line template produces a two-line config -- and converges on the
    rerun like the bundled one.
    """
    template = tmp_path / "local.yml.j2"
    template.write_text(
        'server.host: "{{ kibana_server_host }}"\n'
        "{% if kibana_elasticsearch_username %}"
        "elasticsearch.username: \"{{ kibana_elasticsearch_username }}\"\n"
        "{% endif %}\n",
        encoding="utf-8",
    )
    inventory = make_inventory(
        config_template=str(template), server_host="127.0.0.1", elasticsearch_username="kibana"
    )
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    await run_role(inventory, hosts)

    assert host.remote_files[CONF_PATH] == (
        b'server.host: "127.0.0.1"\nelasticsearch.username: "kibana"\n'
    )

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the notification channel ---------------------------------------------------


def test_only_the_config_requests_notify():
    """The dependency, key, repository, package and service names are not listeners.

    The role's one ``notify:`` sits on the template task; the companion joins it
    for metadata drift (D3). Nothing else in :data:`NOTIFY_RESTART`.
    """
    assert NOTIFY_RESTART == frozenset(
        {
            "Copy Kibana configuration",
            "Set Kibana configuration attributes",
        }
    )
    assert "Ensure dependencies are installed" not in NOTIFY_RESTART
    assert "Add Elasticsearch apt key" not in NOTIFY_RESTART
    assert "Add Kibana repository" not in NOTIFY_RESTART
    assert "Install Kibana" not in NOTIFY_RESTART
    assert "Ensure Kibana is started and enabled at boot" not in NOTIFY_RESTART


def test_frozen_constants_are_the_debian_vars_table():
    """``defaults/main.yml`` + ``setup-Debian.yml`` collapsed to constants."""
    assert DEPENDENCIES == ("apt-transport-https", "gnupg2")
    assert KEY_URL == "https://artifacts.elastic.co/GPG-KEY-elasticsearch"
    assert KEY_PATH == "/etc/apt/trusted.gpg.d/elasticsearch.asc"
    assert KEY_MODE == "0644"
    assert REPO_URL_BASE == "https://artifacts.elastic.co/packages"
    assert SERVICE == "kibana"
    assert BUNDLED_TEMPLATE == "kibana.yml.j2"


# --- the pure helpers, unit-tested directly --------------------------------------


def test_render_matches_the_template_defaults():
    """The Jinja2 render is the template source with every default filled."""
    assert render_kibana_conf(KibanaVars()) == DEFAULT_CONFIG


def test_render_conditional_branches():
    """Each ``{% if %}`` guard fills its branch exactly as the role's did."""
    old = render_kibana_conf(KibanaVars(version="6.x"))
    assert "elasticsearch.url: http://localhost:9200\n" in old
    assert "elasticsearch.hosts" not in old

    authed = render_kibana_conf(
        KibanaVars(elasticsearch_username="kibana", elasticsearch_password="s3cret")
    )
    assert (
        'elasticsearch.username: "kibana"\nelasticsearch.password: "s3cret"\n'
        in authed
    )
    assert '#elasticsearch.username: "user"' not in authed

    # The template's falsiness gate: one half alone keeps the comments.
    half = render_kibana_conf(KibanaVars(elasticsearch_username="kibana"))
    assert '#elasticsearch.username: "user"\n#elasticsearch.password: "pass"\n' in half


def test_kibana_plan_freezes_the_render_and_repo(canned_fetch):
    """The plan is the rendered string plus the repo line and the flags, hashable."""
    plan = kibana_plan(KibanaVars(version="8.x", package="kibana=8.7.1", enabled=False))
    assert isinstance(plan, KibanaPlan)
    assert plan.config_content == render_kibana_conf(KibanaVars(version="8.x"))
    assert plan.repo_src == "deb https://artifacts.elastic.co/packages/8.x/apt stable main"
    assert plan.repo_filename == "kibana-8.x"
    assert plan.package == "kibana=8.7.1"
    assert plan.enabled is False
    assert hash(plan) == hash(
        KibanaPlan(
            config_content=plan.config_content,
            config_file_path=plan.config_file_path,
            package="kibana=8.7.1",
            package_state=plan.package_state,
            service_state=plan.service_state,
            enabled=False,
            repo_src=plan.repo_src,
            repo_filename=plan.repo_filename,
        )
    )


def test_model_is_frozen_and_hashable():
    v = KibanaVars()
    assert hash(v) is not None
    with pytest.raises(ValidationError):
        v.server_port = 5602


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"version": ""}, "version"),
        ({"package": ""}, "package"),
        ({"config_file_path": ""}, "config_file_path"),
        ({"server_host": ""}, "server_host"),
        ({"elasticsearch_url": ""}, "elasticsearch_url"),
        ({"package_state": "installed"}, "package_state"),
        ({"service_state": "removed"}, "service_state"),
        ({"server_port": "not-a-number"}, "server_port"),
        ({"enabled": "maybe"}, "enabled"),
    ],
)
def test_invalid_role_values_fail_fast(overrides, match):
    """The role's implicit type gates are pydantic rejections now (D6)."""
    inventory = make_inventory(**overrides)
    with pytest.raises(ValidationError, match=match):
        resolve_vars(KibanaVars, "kibana", inventory)


def test_numeric_strings_coerce_into_the_integer_fields():
    """The one widening over strict types: pydantic's lax int coercion (D6).

    ``"5602"`` binds to ``server_port`` as ``5602`` (the repo-wide convention,
    as in redis' ``port``) and renders identically, so the fail-fast claim
    covers *unreadable* values, not merely differently-typed ones.
    """
    v = resolve_vars(KibanaVars, "kibana", make_inventory(server_port="5602"))
    assert v["ubuntu"].server_port == 5602
    assert "server.port: 5602\n" in render_kibana_conf(v["ubuntu"])


def test_inventory_role_variables_bind_and_typos_raise():
    """The nested mapping binds bare names; a typo raises RoleVarsError."""
    inventory = make_inventory(
        server_port=5602, server_host="127.0.0.1", elasticsearch_username="kibana"
    )
    v = resolve_vars(KibanaVars, "kibana", inventory)
    assert v["ubuntu"].server_port == 5602
    assert v["ubuntu"].server_host == "127.0.0.1"
    assert v["ubuntu"].elasticsearch_username == "kibana"
    # The RedHat-only surface still carries no extras to trip (D5): every
    # defaults/main.yml name exists on the model.
    assert KibanaVars().config_template == BUNDLED_TEMPLATE

    with pytest.raises(RoleVarsError, match="packagez"):
        resolve_vars(KibanaVars, "kibana", make_inventory(packagez="x"))
