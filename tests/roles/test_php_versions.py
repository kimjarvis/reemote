# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``php_versions`` role (§17.5, §18.11).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, php_versions(v), host_factory=...)``. The factory is
plain (non-``async``) -- the only read is a ``fact`` barrier inside the body,
and the signing-key fetch is ``files.download``'s own control-side step -- so
it is called, not awaited.

The load-bearing assertion is the migration's shape claim: **one checkpoint
carries the whole ``register`` + ``when`` pair** -- ``fact(LinuxDistribution)``
then the setup wave (dependencies, deb822 repository, version purge) closed by
``checkpoint("setup done")`` then the *gated* ``php-common`` purge. That shape
is read straight out of ``report.records``: each barrier record carries its
request type, a fact one naming its ``fact_cls``. On a converged host the gate
closes: the checkpoint reports no change, the gated body yields nothing, and
the wave the docker tests see as a real wave simply is not there.

Two simulation bridges are needed, and both are simulation limits, not library
bugs. The first is the seed: a stock machine here carries *no* PHP at all, so
``seed`` pre-states ``apt_packages`` with the other versions' ``-common``
packages and the shared ``php-common`` -- without them the purge gate could
never fire and the run would be a no-op. The second is the signing key: Sury's
URL serves a *binary* OpenPGP body (the role's deviation D8), installed by
``files.download``, which fetches control-side over HTTPS -- so the
``key_fetch`` fixture monkeypatches **``files._fetch_url_bytes``** (the files
namespace's own seam) with canned binary bytes and records the URLs, keeping
the suite offline.

The outputs are tested through the composition channel itself: the role body
returns :class:`~reemote.roles.php_versions.PhpVersionsOutputs` per host, so
the capture runs the role inside ``scoped("php_versions", ...)`` exactly as a
composing deploy would, and reads the returned dataclass back.
"""

import pytest

import reemote.modules.files.files as files_module
from reemote import run_waves
from reemote.modules.server import LinuxDistribution
from reemote.roles import PhpVersionsOutputs, PhpVersionsVars, php_versions
from reemote.roles.php_versions import (
    DEPENDENCY_PACKAGES,
    OTHER_PHP_COMMON_PACKAGES,
    SURY_KEY_URL,
    distribution_codename,
    php_packages,
    purge_packages,
    resolve_plan,
)
from reemote.rolevars import resolve_vars
from reemote.stages import CheckpointRequest, FactRequest, scoped
from tests.simulated_host import SimulatedHost

SOURCES_PATH = "/etc/apt/sources.list.d/ondrej-php-noble.sources"
KEYRING_PATH = "/etc/apt/keyrings/ondrej-php-noble.gpg"
# The real Sury apt.gpg is a binary OpenPGP keyring, not armored text (D8);
# these canned bytes stand in for it through files.download's binary path.
KEY_BYTES = b"\x99\x01\x00\x04\x5c\x8f\x6f\x9f\x01\x0c\x00\xd3\x0e\x52\x4b"

EXPECTED_SOURCES = (
    "Types: deb\n"
    "URIs: https://packages.sury.org/php\n"
    "Suites: noble\n"
    "Components: main\n"
    "Signed-By: /etc/apt/keyrings/ondrej-php-noble.gpg\n"
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
    """Make the keyring's URL fetch offline and observable.

    ``files.download`` fetches the binary key control-side through the files
    namespace's own ``_fetch_url_bytes`` seam; this stands in for the network
    (D8's reason for not using ``apt.sources_file``'s URL ``signed_by``) and
    records every URL asked for.
    """
    fetched: list[str] = []

    def fake(url: str) -> bytes:
        fetched.append(url)
        return KEY_BYTES

    monkeypatch.setattr(files_module, "_fetch_url_bytes", fake)
    return fetched


def make_inventory(**overrides) -> list[dict]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride in the entry's ``roles`` mapping under
    ``"php_versions"``, bound by ``resolve_vars``. The base entry pins
    ``version`` to ``8.3`` and turns ``install_recommends`` on, so the default
    path exercises the purge of the other four versions and the flag's carry
    onto the outputs; the overrides drive the non-default branches.
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
            "php_versions": {
                "version": "8.3",
                "install_recommends": True,
            },
        },
    }
    entry["roles"]["php_versions"].update(overrides)
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
    """Pre-state: a stock Ubuntu 24.04 machine with four other PHP versions present.

    ``apt_packages`` carries the 8.0/8.1/8.2/8.4 ``-common`` packages (the
    purge list minus the chosen 8.3), the chosen ``php8.3-common`` (which must
    *survive* the purge), and the shared ``php-common`` the gated second purge
    removes -- without them the version purge would change nothing and the
    checkpoint gate could never fire. The Sury artifacts are absent: the first
    pass creates them for real.
    """
    host.linux_distribution = dict(UBUNTU_2404)
    host.apt_packages = {
        "php8.0-common": [""],
        "php8.1-common": [""],
        "php8.2-common": [""],
        "php8.4-common": [""],
        "php8.3-common": [""],
        "php-common": [""],
    }
    host.apt_update_stamp = None
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}


def make_hosts(inventory: list[dict]) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded target."""
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
    v = resolve_vars(PhpVersionsVars, "php_versions", inventory)

    def factory(entry: dict) -> SimulatedHost:
        # The *same* host object every pass, so state survives the barriers.
        return hosts[entry["host"]]

    # A plain (non-async) factory: called, not awaited.
    deploy = php_versions(v)
    report, _hosts = await run_waves(inventory, deploy, host_factory=factory, dry=dry)
    return report


async def run_and_capture(
    inventory: list[dict], hosts: dict[str, SimulatedHost]
) -> tuple[object, dict[str, PhpVersionsOutputs]]:
    """Run the role inside a scope and keep what each host's body returned.

    The outputs are the §9 channel to a composing role, and the role's whole
    published surface -- the Ansible role's thirteen ``set_fact`` aliases
    (deviation D3) -- is only observable this way. The parent here is the shape
    §10.4 shows at a composition site, ``scoped`` label included.
    """
    v = resolve_vars(PhpVersionsVars, "php_versions", inventory)
    captured: dict[str, PhpVersionsOutputs] = {}

    def parent(host):
        captured[host.name] = yield from scoped("php_versions", php_versions(v))(host)

    def factory(entry: dict) -> SimulatedHost:
        return hosts[entry["host"]]

    report, _hosts = await run_waves(inventory, parent, host_factory=factory)
    return report, captured


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


# --- the stream shape (§18.11's falsifier) ------------------------------------


async def test_stream_is_one_fact_and_one_gating_checkpoint(key_fetch):
    """The role's shape: fact -> setup wave -> checkpoint -> gated purge wave.

    Exactly one checkpoint -- the ``register: php_version_purge`` + ``when``
    pair is this role's only mid-stream read (D4) -- and one ``fact`` barrier
    before it. The gated wave only exists because the purge really changed on
    this seeded host, so both request waves are present on pass one.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert _barrier_shape(report) == [
        (0, ("fact", LinuxDistribution)),
        (1, ("checkpoint", "setup done")),
    ]
    # Requests land in two waves: setup (1), the gated php-common purge (2).
    assert _request_waves(report) == {1, 2}


async def test_converged_rerun_drops_the_gated_wave(key_fetch):
    """On a converged host the gate closes *inside* the same stream shape.

    Pass two re-derives both barriers (the body always reads the distribution
    and always closes the setup wave), but nothing changed at the checkpoint,
    so no php-common request is queued and the log carries no entry for it.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    second = await run_role(inventory, hosts)
    assert _barrier_shape(second) == [
        (0, ("fact", LinuxDistribution)),
        (1, ("checkpoint", "setup done")),
    ]
    assert _request_waves(second) == {1}
    assert "Also purge php-common package." not in ubuntu_names(second)


# --- the first pass really deploys the role -----------------------------------


async def test_first_pass_adds_repo_and_purges(key_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    # The repository landed exactly as the deb822_repository task wrote it,
    # and the binary keyring under the repo's own codename-derived filename
    # (D8's files.download, not the URL signed_by form).
    assert host.remote_files[SOURCES_PATH].decode() == EXPECTED_SOURCES
    assert host.remote_files[KEYRING_PATH] == KEY_BYTES
    assert host.file_stats[KEYRING_PATH]["mode"] == 644
    assert key_fetch == [SURY_KEY_URL]

    # The index refresh chained onto the real sources change (D1, issue #14).
    assert host.apt_update_stamp is not None

    # Dependencies installed; the other versions' -common packages purged; the
    # chosen version's -common survives; the gated php-common purge ran too.
    for package in DEPENDENCY_PACKAGES:
        assert package in host.apt_packages
    for name in OTHER_PHP_COMMON_PACKAGES:
        if name != "php8.3-common":
            assert name not in host.apt_packages
    assert "php8.3-common" in host.apt_packages
    assert "php-common" not in host.apt_packages

    # Every request of the first pass reported change, gate included.
    log = ubuntu_log(report)
    assert log["Add dependencies for PHP versions."].changed
    assert log["Install Ondrej's PHP repo key."].changed
    assert log["Add Ondrej's PHP repo."].changed
    assert log["Purge other PHP versions."].changed
    assert log["Also purge php-common package."].changed


async def test_second_pass_is_a_noop(key_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    # The migration's idempotence claim: dependencies present, repo and
    # keyring byte-identical, the purge list already gone -- so nothing
    # changed anywhere, and with no change at the checkpoint the gated
    # php-common wave never ran.
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


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


async def test_dry_preview_stops_at_the_distribution_barrier(key_fetch):
    """``dry=True`` never runs a barrier, so it parks on the first item.

    php_versions' first yielded item is the ``LinuxDistribution`` ``fact``
    barrier, so the dry preview plans no request and applies nothing -- the
    whole stream stays unexecuted.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts, dry=True)

    assert host.apt_update_stamp is None
    assert SOURCES_PATH not in host.remote_files
    assert "php8.0-common" in host.apt_packages
    assert changed_entries(report) == []
    # Exactly one barrier record, no request records, and no key was fetched.
    assert _barrier_shape(report) == [(0, ("fact", LinuxDistribution))]
    assert _request_waves(report) == set()
    assert key_fetch == []


# --- the checkpoint gate --------------------------------------------------------


async def test_gate_refires_when_a_version_reappears(key_fetch):
    """The ``when: php_version_purge.changed`` gate is live on every pass.

    Converged pass one, then a fresh apt run behind the role's back reinstalls
    php8.1-common *and* php-common. The next purge removes the version again --
    the checkpoint sees a change -- so the php-common request is queued once
    more and really removes it. A role that had flattened the gate into the
    setup wave (or a when= read of stale state) would skip it here.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    host = hosts["ubuntu"]
    host.apt_packages["php8.1-common"] = [""]
    host.apt_packages["php-common"] = [""]

    second = await run_role(inventory, hosts)
    log = ubuntu_log(second)
    assert log["Purge other PHP versions."].changed
    assert "Also purge php-common package." in log
    assert log["Also purge php-common package."].changed
    assert "php8.1-common" not in host.apt_packages
    assert "php-common" not in host.apt_packages


# --- the published outputs (deviation D3) ---------------------------------------


async def test_outputs_publish_the_set_fact_aliases(key_fetch):
    """A composing capture gets the thirteen Debian-path aliases, resolved.

    Run through ``scoped`` -- the §10.4 composition site itself -- so this is
    exactly what ``yield from scoped("php_versions", ...)`` hands back on both
    hosts, including the guard host's empty ``version=None`` return.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    _report, captured = await run_and_capture(inventory, hosts)

    out = captured["ubuntu"]
    assert out.version == "8.3"
    assert out.packages == php_packages("8.3")
    assert "php8.3-json" not in out.packages
    assert "php8.3-opcache" in out.packages
    assert out.conf_paths == (
        "/etc/php/8.3/fpm",
        "/etc/php/8.3/apache2",
        "/etc/php/8.3/cli",
    )
    assert out.extension_conf_paths == tuple(
        f"{path}/conf.d" for path in out.conf_paths
    )
    assert out.fpm_daemon == "php8.3-fpm"
    assert out.fpm_conf_path == "/etc/php/8.3/fpm"
    assert out.fpm_pool_conf_path == "/etc/php/8.3/fpm/pool.d/www.conf"
    assert out.mysql_package == "php8.3-mysql"
    assert out.redis_package == "php8.3-redis"
    assert out.memcached_package == "php8.3-memcached"
    assert out.pgsql_package == "php8.3-pgsql"
    module_path = "/usr/lib/php/8.3/modules"
    assert (
        out.tideways_module_path
        == out.uploadprogress_module_path
        == out.xdebug_module_path
        == out.xhprof_module_path
        == module_path
    )
    assert out.opcache_conf_filename == "10-opcache.ini"
    # D7: the flag reaches no request, only the outputs.
    assert out.install_recommends is True

    # The guard host still returns outputs -- the composition channel never
    # sees a missing value.
    assert captured["web1"] == PhpVersionsOutputs(version=None)


# --- the pure helpers, unit-tested directly --------------------------------------


def test_php_packages_interpolates_and_gates():
    # 7.4 keeps both bundled-later entries; 8.3 drops json; 8.5 drops opcache.
    full = php_packages("7.4")
    assert "php7.4-json" in full and "php7.4-opcache" in full
    assert len(full) == 15
    mid = php_packages("8.3")
    assert "php8.3-json" not in mid and "php8.3-opcache" in mid
    assert len(mid) == 14
    newest = php_packages("8.5")
    assert "php8.5-json" not in newest and "php8.5-opcache" not in newest
    assert len(newest) == 13
    # The chosen version's own -common is always in the list.
    assert "php8.3-common" in mid


def test_purge_packages_excludes_the_chosen_version():
    assert purge_packages("8.3") == (
        "php8.0-common",
        "php8.1-common",
        "php8.2-common",
        "php8.4-common",
    )
    # A version outside the list purges every entry (D6's substring shape).
    assert purge_packages("7.4") == OTHER_PHP_COMMON_PACKAGES


def test_distribution_codename_reads_release_meta():
    assert distribution_codename(UBUNTU_2404) == "noble"
    with pytest.raises(ValueError, match="VERSION_CODENAME"):
        distribution_codename({"name": "Ubuntu"})
    with pytest.raises(ValueError, match="VERSION_CODENAME"):
        distribution_codename({"release_meta": {"VERSION_CODENAME": ""}})


def test_resolve_plan_names_the_repo_like_the_role():
    plan = resolve_plan(PhpVersionsVars(version="8.3"), dict(UBUNTU_2404))
    assert plan.version == "8.3"
    assert plan.codename == "noble"
    assert plan.repo_filename == "ondrej-php-noble"
    assert plan.purge_packages == purge_packages("8.3")


# --- the typed variables reject bad input ----------------------------------------


def test_vars_reject_a_non_version_string():
    # pydantic's ValidationError is a ValueError; the message carries the
    # validator's own wording. The old raise site never existed -- Ansible
    # only found out at the first ``is version(...)`` test.
    with pytest.raises(ValueError, match="dotted digits"):
        PhpVersionsVars(version="8.x")
    with pytest.raises(ValueError, match="dotted digits"):
        PhpVersionsVars(version="")


def test_vars_are_frozen():
    hv = PhpVersionsVars()
    assert hv.version == "8.3"
    assert hv.install_recommends is False
    with pytest.raises(ValueError):
        hv.version = "8.1"
