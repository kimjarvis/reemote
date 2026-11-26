# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``nodejs`` role (§17.5, §18.12).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, nodejs(v), host_factory=...)``. The factory is plain
(non-``async``) -- there is no barrier, so no factory IO either -- so it is
called, not awaited.

The load-bearing assertion is the migration's shape claim: **zero barriers and
one request wave** -- nothing here needs a read of the machine before a request
can be built, so the whole role (dependencies, repository, ``nodejs`` install,
npm global directory, profile script, global packages) lands in wave 0 and no
``fact``/``act``/``checkpoint``/``control`` record exists. A speculatively-added
barrier breaks the shape test rather than hiding.

Because there is no barrier, ``dry=True`` does not park early the way the
java/docker/repo_epel tests observe: it plans the ENTIRE wave -- reading each
fact and firing the control-side signing-key fetch -- and applies nothing
(deviation D9). The ``key_fetch`` fixture monkeypatches
**``apt._fetch_url_bytes``** (the apt namespace's own seam) with canned armored
bytes and records the URLs, keeping the suite offline while proving the fetch
fires in both the dry and the apply pass.

No simulation bridge is needed for idempotence: the simulation's own mutators
maintain every seam the second pass reads -- ``install_apt_packages`` records
the dependency packages and the ``nodejs=16.*`` glob pin verbatim (satisfied on
the rerun via ``fnmatchcase``, issue #41), ``install_apt_sources_file`` the
deb822 file and its keyring, ``make_directory``/``chown_path`` the npm prefix
row, ``install_file`` the profile content and its stat row, ``install_npm_packages``
the global-package store, and the chained ``AptUpdateCommand`` refreshes
``apt_update_stamp``.
"""

import json

import pytest

import reemote.modules.apt.apt as apt_module
from reemote import run_waves
from reemote.roles import NodejsVars, NpmGlobalPackage, nodejs
from reemote.roles.nodejs import (
    DEFAULT_NPM_CONFIG_PREFIX,
    DEPENDENCY_PACKAGES,
    NODESOURCE_KEY_URL,
    NPM_PROFILE,
    nodejs_plan,
    npm_spec,
    partition_npm_packages,
    render_npm_profile,
    resolve_npm_config,
    resolve_npm_user,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from reemote.stages import CheckpointRequest, FactRequest
from tests.simulated_host import SimulatedHost

VERSION = "16.x"
FILENAME = f"nodesource_{VERSION}"
SOURCES_PATH = f"/etc/apt/sources.list.d/{FILENAME}.sources"
KEYRING_PATH = f"/etc/apt/keyrings/{FILENAME}.asc"
NODEJS_PIN = "nodejs=16.*"
NPM_SCOPE = f"global@{DEFAULT_NPM_CONFIG_PREFIX}"
KEY_BYTES = (
    b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nfake\n-----END PGP PUBLIC KEY BLOCK-----\n"
)
EXPECTED_SOURCES = (
    "Types: deb\n"
    f"URIs: https://deb.nodesource.com/node_{VERSION}\n"
    "Suites: nodistro\n"
    "Components: main\n"
    f"Signed-By: {KEYRING_PATH}\n"
)
PROFILE_CONTENT = render_npm_profile(DEFAULT_NPM_CONFIG_PREFIX).encode()


@pytest.fixture
def key_fetch(monkeypatch):
    """Make the ``signed_by`` URL fetch offline and observable.

    ``apt.sources_file`` fetches the ASCII-armored key control-side through the
    apt namespace's own ``_fetch_url_bytes`` seam (not ``files``' -- the
    namespaces never import each other); this stands in for the network, the
    same seam the docker test patches, and records every URL asked for.
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
    ``"nodejs"``, bound by ``resolve_vars``. The base entry sets
    ``npm_global_packages`` so the default path exercises the global-package
    loop; the overrides drive the
    non-default branches (``npm_config_prefix`` on the raw extra channel, the
    profile skip, the state partition, ``package.json``).
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
            "nodejs": {
                "npm_global_packages": ["npm-check-updates"],
            },
        },
    }
    entry["roles"]["nodejs"].update(overrides)
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
    """Pre-state: a stock minimal Ubuntu machine with no Node.js or npm.

    No apt packages, no sources, an unrefreshed cache (the chained update runs),
    no npm prefix directory, no profile script and no global npm packages, so
    every request is a real change on the first pass.
    """
    host.apt_packages = {}
    host.apt_sources = []
    host.apt_update_stamp = None
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}
    host.directory_stats = {}
    host.npm_packages = {}
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
    v = resolve_vars(NodejsVars, "nodejs", inventory)

    def factory(entry: dict) -> SimulatedHost:
        # The *same* host object every pass, so state survives the run.
        return hosts[entry["host"]]

    # A plain (non-async) factory: called, not awaited.
    deploy = nodejs(v)
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

    This role yields no barrier at all, so the shape list must be empty; the
    classifier exists so a speculatively-added ``fact``/``checkpoint``/``act``/
    ``control`` barrier shows up as a distinct label and breaks the shape test
    rather than hiding (§18.12).
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
    """The set of wave numbers that carried at least one applied ubuntu request."""
    return {
        r.wave for r in report.records if r.host == "ubuntu" and r.log is not None
    }


def _planned_waves(report) -> set[int]:
    """The set of wave numbers that carried at least one planned ubuntu request.

    Dry mode plans but never applies, so ``log`` is ``None`` while ``planned``
    is set -- this is the dry-run analogue of ``_request_waves`` and is how the
    D9 preview (the whole wave planned, nothing applied) is observed.
    """
    return {
        r.wave for r in report.records if r.host == "ubuntu" and r.planned is not None
    }


# --- the stream shape (the barrier-free falsifier) -----------------------------


async def test_stream_is_barrier_free_and_one_request_wave(key_fetch):
    """The shape claim: no barrier at all, the whole role in wave 0.

    Nothing needs a read of the machine before a request can be built, so there
    is no ``fact``/``act``/``checkpoint``/``control`` record and every request
    lands in the single wave 0. The rerun re-derives the same shape.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert _barrier_shape(report) == []
    assert _request_waves(report) == {0}

    second = await run_role(inventory, hosts)
    assert _barrier_shape(second) == []
    assert _request_waves(second) == {0}


# --- the first pass really deploys the role ------------------------------------


async def test_first_pass_installs_nodejs_and_wires_npm(key_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    # The dependencies and the version-pinned nodejs package are installed; the
    # glob pin is recorded verbatim (issue #41).
    for package in DEPENDENCY_PACKAGES:
        assert package in host.apt_packages
    assert "nodejs" in host.apt_packages
    assert host.apt_packages["nodejs"] == ["16.*"]

    # The three repository tasks folded into one sources_file (D1): the deb822
    # file, the repo-named keyring the URL signed_by fetched control-side, and
    # the chained cache refresh.
    assert host.remote_files[SOURCES_PATH].decode() == EXPECTED_SOURCES
    assert host.remote_files[KEYRING_PATH] == KEY_BYTES
    assert host.file_stats[KEYRING_PATH]["mode"] == 644
    assert host.apt_update_stamp is not None
    assert key_fetch == [NODESOURCE_KEY_URL]

    # The global npm directory, owned by the connection's login user (D4).
    assert host.directory_stats[DEFAULT_NPM_CONFIG_PREFIX] == {
        "mode": 755,
        "user": "supervisor",
        "group": "supervisor",
    }

    # The profile script: the f-string render, root-owned 0644.
    assert host.remote_files[NPM_PROFILE] == PROFILE_CONTENT
    assert host.file_stats[NPM_PROFILE] == {
        "user": "root",
        "group": "root",
        "mode": 644,
        "size": len(PROFILE_CONTENT),
    }

    # The global npm package landed in the relocated prefix's scope.
    assert host.npm_packages[NPM_SCOPE]["npm-check-updates"] == ""

    log = ubuntu_log(report)
    assert log["Ensure NodeSource dependencies are present"].changed
    assert log["Add NodeSource repository for Node.js"].changed
    assert log["Ensure Node.js and npm are installed"].changed
    assert log["Create npm global directory"].changed
    assert log["Add npm prefix bin directory to the global PATH"].changed
    # The mode companion is a no-op on the first pass: files.install writes with
    # the task's mode, so there is no drift to converge yet.
    assert log["Set npm profile script mode"].changed is False
    assert log["Ensure npm global packages are installed"].changed


async def test_mode_drift_is_converged_by_the_companion(key_fetch):
    """The files.file companion's whole purpose: content-only install idempotence.

    With the content byte-identical but the mode drifted, files.install reports
    no change while the paired files.file re-applies 0644 -- what the role's
    ``template: mode=0644`` enforces on every run (the java/apache precedent).
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    await run_role(inventory, hosts)

    host = hosts["ubuntu"]
    host.file_stats[NPM_PROFILE]["mode"] = 640

    report = await run_role(inventory, hosts)
    log = ubuntu_log(report)
    assert log["Add npm prefix bin directory to the global PATH"].changed is False
    assert log["Set npm profile script mode"].changed
    assert host.file_stats[NPM_PROFILE]["mode"] == 644


async def test_second_pass_is_a_noop(key_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    # The migration's idempotence claim, with no bridge: the packages are
    # installed (the glob pin satisfied), the sources file and keyring match,
    # the stamp is fresh, the npm directory and the profile content and mode all
    # match, and the global package is present.
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_non_ubuntu_host_is_never_touched(key_fetch):
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    skipped = hosts["web1"]

    # The family guard returns before the first yield, so the off-target host is
    # in no wave and emits no log line at all.
    assert not any(entry.host == "web1" for entry in report.merged().log)
    assert not any(r.host == "web1" for r in report.records)
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}
    assert skipped.directory_stats == {}
    assert skipped.npm_packages == {}


async def test_dry_preview_plans_the_whole_wave(key_fetch):
    """D9: no barrier to park on, so ``dry=True`` plans everything, applies nothing.

    Unlike java/docker/repo_epel, whose dry preview parks on the first ``fact``
    barrier having planned nothing, this role yields requests from its first
    item: the dry pass materializes every request -- reading each fact and
    firing the control-side signing-key fetch -- and applies nothing.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts, dry=True)

    assert _barrier_shape(report) == []
    # The whole wave is planned (planned set, log None), but nothing applied.
    assert _planned_waves(report) == {0}
    assert _request_waves(report) == set()
    assert changed_entries(report) == []
    # The control-side fetch still fires in the dry pass -- the preview is the
    # whole wave, not a one-barrier stub.
    assert key_fetch == [NODESOURCE_KEY_URL]
    # Nothing mutated the host.
    assert host.apt_packages == {}
    assert host.apt_update_stamp is None
    assert host.remote_files == {}
    assert host.directory_stats == {}
    assert host.npm_packages == {}


# --- the extra-channel and branch variations -----------------------------------


async def test_npm_config_prefix_rides_the_raw_extra_channel(key_fetch):
    """D5: ``npm_config_prefix`` is read from ``host.extra``, not a typed field.

    The knob is not a ``NodejsVars`` field, so ``resolve_vars`` never sees it;
    the role reads it from the raw top-level extra channel, preserving the
    inventory spelling. The directory, the profile script
    and the npm scope all follow the relocated prefix.
    """
    inventory = make_inventory()
    inventory[0]["npm_config_prefix"] = "/opt/npm"  # raw extra channel
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    await run_role(inventory, hosts)

    assert host.directory_stats["/opt/npm"]["user"] == "supervisor"
    assert DEFAULT_NPM_CONFIG_PREFIX not in host.directory_stats
    assert host.remote_files[NPM_PROFILE] == render_npm_profile("/opt/npm").encode()
    assert host.npm_packages["global@/opt/npm"]["npm-check-updates"] == ""

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_generate_etc_profile_false_skips_the_profile(key_fetch):
    """``nodejs_generate_etc_profile=false`` skips the template task entirely."""
    inventory = make_inventory(generate_etc_profile=False)
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    report = await run_role(inventory, hosts)

    assert NPM_PROFILE not in host.remote_files
    names = ubuntu_names(report)
    assert "Add npm prefix bin directory to the global PATH" not in names
    assert "Set npm profile script mode" not in names


async def test_global_packages_partition_by_state(key_fetch):
    """D7: the per-item ``state`` splits into present / latest / absent requests."""
    inventory = make_inventory(
        npm_global_packages=[
            {"name": "present-pkg"},
            {"name": "pinned-pkg", "version": "1.2.3"},
            {"name": "latest-pkg", "state": "latest"},
            {"name": "absent-pkg", "state": "absent"},
        ]
    )
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    # Seed the package the absent request must remove.
    host.npm_packages[NPM_SCOPE] = {"absent-pkg": ""}

    report = await run_role(inventory, hosts)
    names = ubuntu_names(report)
    assert "Ensure npm global packages are installed" in names
    assert "Ensure npm global packages are up to date" in names
    assert "Ensure npm global packages are removed" in names

    store = host.npm_packages[NPM_SCOPE]
    assert store["present-pkg"] == ""
    assert store["pinned-pkg"] == "1.2.3"  # the ``<pkg>=<version>`` pin (D7)
    assert store["latest-pkg"] == ""
    assert "absent-pkg" not in store


async def test_package_json_path_installs_the_manifest(key_fetch):
    """The opt-in ``package.json`` install (the role's ``when: ... != ""``)."""
    manifest = {"dependencies": {"left-pad": "^1.3.0"}}
    inventory = make_inventory(package_json_path="/srv/app/package.json")
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    host.remote_files["/srv/app/package.json"] = json.dumps(manifest).encode()

    report = await run_role(inventory, hosts)

    assert "Install packages defined in a given package.json" in ubuntu_names(report)
    # The manifest's dependencies land in the manifest's own directory scope.
    assert host.npm_packages["directory=/srv/app"]["left-pad"] == ""


# --- the fail-fast gates, through the driver ------------------------------------


async def test_missing_npm_user_raises_before_any_request(key_fetch):
    """D4: no ``nodejs_install_npm_user`` and no connection login name raises."""
    inventory = make_inventory()
    # ``connection`` is a top-level entry key, not a role variable.
    inventory[0]["connection"] = {
        "address": "192.0.2.10",
        "key_path": "~/.ssh/id_ed25519",
    }
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    with pytest.raises(ValueError, match="npm install user"):
        await run_role(inventory, hosts)

    # The raise is in the body before the request wave is built.
    assert host.apt_packages == {}
    assert host.remote_files == {}


async def test_explicit_install_npm_user_wins(key_fetch):
    """D4: ``nodejs_install_npm_user`` overrides the connection login name."""
    inventory = make_inventory(install_npm_user="deploy")
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    await run_role(inventory, hosts)

    assert host.directory_stats[DEFAULT_NPM_CONFIG_PREFIX]["user"] == "deploy"


@pytest.mark.parametrize(
    ("extra", "error", "match"),
    [
        ({"npm_config_prefix": ""}, ValueError, "non-empty path"),
        ({"npm_config_prefix": 42}, TypeError, "must be a string path"),
        ({"npm_config_unsafe_perm": "yes"}, TypeError, "must be a boolean"),
    ],
)
async def test_bad_npm_config_extra_raises(key_fetch, extra, error, match):
    """D5: a wrong-typed or empty ``npm_config_*`` extra key fails before the wave."""
    inventory = make_inventory()
    inventory[0].update(extra)  # raw extra channel, top-level
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    with pytest.raises(error, match=match):
        await run_role(inventory, hosts)

    assert host.apt_packages == {}
    assert host.remote_files == {}


# --- the pure helpers, unit-tested directly -------------------------------------


def test_render_npm_profile_matches_the_template():
    """The f-string render is the template source with the variable filled."""
    assert render_npm_profile("/usr/local/lib/npm") == (
        "export PATH=$PATH:/usr/local/lib/npm/bin\n"
        "export NPM_CONFIG_PREFIX=/usr/local/lib/npm\n"
        "export NODE_PATH=$NODE_PATH:/usr/local/lib/npm/lib/node_modules\n"
    )
    # NODE_PATH is exported verbatim in the profile even though the npm commands
    # drop it (D6); the trailing newline matches the template byte-for-byte.
    assert render_npm_profile("/x").endswith("\n")
    assert "NODE_PATH" in render_npm_profile("/x")


def test_npm_spec_and_partition():
    assert npm_spec(NpmGlobalPackage(name="a")) == "a"
    assert npm_spec(NpmGlobalPackage(name="a", version="1.0")) == "a=1.0"

    packages = (
        NpmGlobalPackage(name="p"),
        NpmGlobalPackage(name="q", version="2.0"),
        NpmGlobalPackage(name="r", state="latest"),
        NpmGlobalPackage(name="s", state="absent"),
    )
    present, latest, absent = partition_npm_packages(packages)
    assert present == ("p", "q=2.0")
    assert latest == ("r",)
    assert absent == ("s",)


def test_resolve_npm_user_prefers_the_explicit_var():
    conn = {"username": "conn-user"}
    assert resolve_npm_user("explicit", conn) == "explicit"
    assert resolve_npm_user("", conn) == "conn-user"
    assert resolve_npm_user("", {"user": "asyncssh-user"}) == "asyncssh-user"
    with pytest.raises(ValueError, match="npm install user"):
        resolve_npm_user("", {"address": "192.0.2.10"})


def test_resolve_npm_config_reads_the_raw_extra_channel():
    # Defaults when the knobs are absent.
    assert resolve_npm_config({}) == (DEFAULT_NPM_CONFIG_PREFIX, False)
    # Present values pass through; unrelated keys are ignored.
    assert resolve_npm_config(
        {"npm_config_prefix": "/opt/npm", "npm_config_unsafe_perm": True, "other": 1}
    ) == ("/opt/npm", True)
    with pytest.raises(ValueError, match="non-empty path"):
        resolve_npm_config({"npm_config_prefix": ""})
    with pytest.raises(TypeError, match="string path"):
        resolve_npm_config({"npm_config_prefix": 7})
    with pytest.raises(TypeError, match="boolean"):
        resolve_npm_config({"npm_config_unsafe_perm": "no"})


def test_nodejs_plan_derives_the_repository_and_pin():
    hv = NodejsVars(version="20.x")
    plan = nodejs_plan(hv, "supervisor", "/usr/local/lib/npm", False)
    assert plan.sources_filename == "nodesource_20.x"
    assert plan.repo_uri == "https://deb.nodesource.com/node_20.x"
    # The role's ``regex_replace('x', '') + '*'``.
    assert plan.nodejs_pin == "nodejs=20.*"
    assert plan.npm_user == "supervisor"
    assert plan.profile_content == render_npm_profile("/usr/local/lib/npm")
    assert plan.package_json is None

    # generate_etc_profile=False drops the profile; a package.json path is kept.
    plan = nodejs_plan(
        NodejsVars(generate_etc_profile=False, package_json_path="/srv/app/package.json"),
        "deploy",
        "/opt/npm",
        True,
    )
    assert plan.profile_content is None
    assert plan.package_json == "/srv/app/package.json"
    assert plan.npm_config_prefix == "/opt/npm"
    assert plan.npm_config_unsafe_perm is True


# --- the variables model ---------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"version": ""}, "version"),
        ({"npm_global_packages": [{"name": ""}]}, "non-empty"),
        ({"npm_global_packages": [{"name": "x", "state": "bogus"}]}, "state"),
        ({"npm_global_packages": "not-a-list"}, "npm_global_packages"),
        ({"unknown": "x"}, "unknown"),  # extra="forbid"
        # npm_config_* are NOT typed fields, so a nodejs_-prefixed spelling of
        # them is a typo against the model, not a binding (D5).
        ({"npm_config_prefix": "/opt"}, "npm_config_prefix"),
    ],
)
def test_invalid_role_values_fail_fast(overrides, match):
    with pytest.raises(ValueError, match=match):
        NodejsVars.model_validate(overrides)


def test_bare_string_package_coerces_to_a_model():
    """The role's ``with_items`` accepts a bare string or a mapping."""
    hv = NodejsVars.model_validate({"npm_global_packages": ["node-sass", {"name": "x"}]})
    assert hv.npm_global_packages == (
        NpmGlobalPackage(name="node-sass"),
        NpmGlobalPackage(name="x"),
    )


def test_inventory_role_variables_bind_and_typos_raise():
    """The nested mapping binds bare names; a misspelling raises RoleVarsError."""
    inventory = make_inventory(version="18.x")
    v = resolve_vars(NodejsVars, "nodejs", inventory)
    hv = v.for_host(SimulatedHost.from_inventory(inventory[0]))
    assert hv.version == "18.x"
    assert hv.npm_global_packages == (NpmGlobalPackage(name="npm-check-updates"),)

    typo = make_inventory(verson="18.x")
    with pytest.raises(RoleVarsError, match="verson"):
        resolve_vars(NodejsVars, "nodejs", typo)


def test_model_is_frozen_and_hashable():
    hv = NodejsVars(version="20.x")
    assert hash(hv) is not None
    with pytest.raises(ValueError):  # pydantic frozen-instance error
        hv.version = "18.x"
