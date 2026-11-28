# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``daemonize`` role (design doc section 17.7).

The role is library code, so it is imported (``from reemote.roles import
daemonize``) and replayed against ``SimulatedHost`` through the real wave
driver: ``run_waves(inventory, daemonize(vars_set), host_factory=...)``. The
Ansible play's shape -- four tasks, a ``register``, a gated ``command`` loop --
is one pass of one deploy here: the setup requests, the ``which`` read as a
``fact`` barrier, and (only on a host the probe found bare) the build
``act``.

Four assertions carry this migration's claims:

* **the in-stream read.** ``barrier_names`` shows exactly one barrier on the
  fresh host -- ``Which`` -- and the build lands in the wave *after* it, so the
  branch provably runs on the barrier's answer, not on a frozen plan.
* **the gate converges through the machine, not the test.** A second pass
  against the same persistent host fetches no tarball (the ``creates`` marker
  extracted on pass 1 answers the gate), runs no shell (the :class:`BuildHost`
  registered ``daemonize`` on ``PATH`` when the build succeeded), and changes
  nothing.
* **the family guard.** The RedHat host emits no requests, no barriers, no log
  records and mutates no store.
* **the build failure is the body's error.** A non-zero ``make`` exits the run
  as ``CommandError`` -- the barrier's ``Result`` is data, the role raises.

Seeding choices, none of them a bridge:

* ``archive_members`` is keyed on the URL spool path ``files.unarchive``
  stages to, and the listing carries the ``daemonize-release-1.7.7/``
  **directory** member and ``INSTALL``: those are what make the operation's
  in-flight ``creates`` gate converge, so pass 2 never fetches.
* ``shell_results`` is keyed by the build's verbatim ``sudo`` rendering, and
  the binary ``make install`` would leave behind -- plus its ``PATH`` entry --
  is planted by :class:`BuildHost`, a ``SimulatedHost`` subclass whose
  ``_run_shell`` records them. A simulation limit, not a library bug: a real
  ``make install`` writes ``/usr/sbin/daemonize`` and the next
  ``command -v`` finds it.
* the workspace directory is seeded (``/root`` exists on a real host), the
  source tree is not: pass 1's ``files.unarchive`` creates it.
* the control-side tarball fetch is stubbed by the ``canned_fetch`` fixture,
  asked for explicitly by every test that reaches the extraction: SimulatedHost
  does not intercept the control-side fetch.
"""

import posixpath
from typing import Any

import pytest
from pydantic import ValidationError

from reemote import CommandError, resolve_vars, run_waves
from reemote.modules.files import files as files_module
from reemote.modules.files.files import _unarchive_spool_path
from reemote.roles import DaemonizeVars, daemonize
from reemote.roles.daemonize import (
    BUILD_NAME,
    SETUP_NAME,
    TARBALL_URL,
    TOOLCHAIN_NAME,
    VERSION,
    WORKSPACE,
    build_command,
    resolve_daemonize,
)
from tests.simulated_host import SimulatedHost

# The role reads no distribution fact (the family guard reads the inventory's
# declared ``family``), but the simulated apt seam answers from these stores,
# so a stock Ubuntu 24.04 machine is still what the host starts as.
DISTRO = {"name": "Ubuntu", "family": "Debian", "major": 24, "minor": 0, "release_meta": {}}

#: A gzip-magic binary body (never valid UTF-8): the canned control-side
#: tarball, opaque to the simulation, which reads only ``archive_members``.
FAKE_TARBALL = b"\x1f\x8b\x08\x00\x00\x00\x00\x00fake-daemonize-tarball"

#: The exact shell the ``sudo=True`` build action renders (the inventory
#: carries no privilege section, so a bare ``sudo`` on the explicit ``sh -c``
#: chain): the seam key ``SimulatedHost._run_shell`` answers from.
BUILD_SHELL = (
    "sudo sh -c 'cd /root/daemonize-release-1.7.7"
    " && ./configure --prefix=/usr && make && make install'"
)


# --- fixtures and rig --------------------------------------------------------


@pytest.fixture
def canned_fetch(monkeypatch):
    """Replace the control-side tarball fetch with a canned-bytes stub.

    ``files.unarchive`` looks ``_fetch_url_bytes`` up as a module global at call
    time, so patching the module attribute redirects the fetch. Returns the list
    of URLs the stub was called with, so a test can assert the fetch happened
    -- and that a converged second pass fetches nothing, which is the
    ``creates`` gate.
    """
    fetched: list[str] = []

    def fake_fetch(url: str) -> bytes:
        fetched.append(url)
        return FAKE_TARBALL

    monkeypatch.setattr(files_module, "_fetch_url_bytes", fake_fetch)
    return fetched


def make_inventory(**roles: dict[str, Any]) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride on the entry's ``daemonize`` role mapping, written
    with the model's bare field names. Each keyword argument names a role and
    updates that role's mapping, so a test changes a variable exactly the way
    an operator does.
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
        "roles": {"daemonize": {}},
    }
    for role, variables in roles.items():
        entry["roles"].setdefault(role, {}).update(variables)
    return [
        entry,
        {
            "host": "web1",
            "family": "RedHat",
            "connection": {"address": "192.0.2.11", "username": "admin"},
            "group": "centos",
        },
    ]


def plan_for(entry: dict[str, Any]) -> Any:
    """The role's own :class:`DaemonizePlan` for one inventory entry.

    Built the way the body builds it -- ``resolve_daemonize`` over the bound
    model -- so the seed keys (the spool path, the build shell) are the values
    the real run will use rather than a second copy of the path arithmetic.
    """
    return resolve_daemonize(DaemonizeVars.model_validate(entry["roles"]["daemonize"]))


def _regular_file(host: SimulatedHost, path: str, content: bytes, mode: int = 644) -> None:
    """Seed a regular file across the three stores its facts read."""
    host.remote_files[path] = content
    host.existing_files.add(path)
    host.file_stats[path] = {
        "user": "root",
        "group": "root",
        "mode": mode,
        "size": len(content),
    }


class BuildHost(SimulatedHost):
    """A simulated host whose build action installs the binary it claims to.

    The ``Shell`` seam answers from ``shell_results`` and mutates no store, so
    a role that builds a binary and then branches on ``PATH`` needs the product
    planted. Under ``run_waves`` the ``Which`` fact of the *next pass* reads the
    stores directly, so recording them inside the seam is enough: no test code
    runs between the action and the rerun, which is the point of the wave
    stream. A simulation limit, not a library bug -- a real ``make install``
    writes ``<prefix>/sbin/daemonize`` (the upstream Makefile's installdir).

    ``installed_bin`` is set per host by :func:`make_hosts` from that host's own
    resolved plan, so a test that overrides ``install_path`` still converges.
    """

    #: Overridden per host; the default is the role default's install target.
    installed_bin = "/usr/sbin/daemonize"

    async def _run_shell(self, shell: str) -> Any:
        result = await super()._run_shell(shell)
        if result.exit_status == 0 and "&& make && make install" in shell:
            _regular_file(self, self.installed_bin, FAKE_TARBALL, mode=755)
            self.command_paths["daemonize"] = self.installed_bin
        return result


def seed(host: BuildHost, entry: dict[str, Any], *, build_exit: int = 0) -> None:
    """Pre-state: a stock Ubuntu machine with no toolchain, tree or binary.

    Nothing daemonize-related exists: ``apt_packages`` is empty (so the
    toolchain request changes), the workspace exists but holds no source tree
    (``/root`` does on a real host), ``command_paths`` carries no
    ``daemonize`` (so the probe reads ``None`` and the build runs).
    """
    host.linux_distribution = dict(DISTRO)
    host.apt_packages = {}
    host.apt_update_stamp = None
    host.remote_files = {}
    host.existing_files = set()
    host.file_stats = {}
    host.links = {}
    host.link_stats = {}
    host.directory_stats = {WORKSPACE: {"mode": 700, "user": "root", "group": "root"}}
    host.existing_files |= set(host.directory_stats)
    host.services = {}
    host.enabled_services = set()
    host.command_paths = {}

    plan = plan_for(entry)
    # The upstream Makefile installs into ``${exec_prefix}/sbin``, not ``bin``
    # -- verified on the live host, which ends up with /usr/sbin/daemonize.
    host.installed_bin = f"{plan.install_path}/sbin/daemonize"
    # The directory member (trailing slash) plus INSTALL: the extraction
    # registers them, so pass 2 answers the unarchive `creates` gate and never
    # fetches -- the same converge-the-listing trick as the php_xdebug suite.
    base = posixpath.basename(plan.src_dir)
    host.archive_members = {
        _unarchive_spool_path(plan.tarball_url): (
            base + "/",
            f"{base}/INSTALL",
            f"{base}/configure",
            f"{base}/Makefile.in",
            f"{base}/daemonize.c",
        )
    }
    host.shell_results["sudo " + build_command(plan)] = {
        "exit_status": build_exit,
        "stdout": "",
        "stderr": "" if build_exit == 0 else "make: *** [Makefile:2: all] Error 1",
    }


def make_hosts(
    inventory: list[dict[str, Any]], *, build_exit: int = 0
) -> dict[str, BuildHost]:
    """Materialize the inventory into persistent simulated hosts, seeded target.

    Only the Ubuntu entry is seeded, so the RedHat host keeps its stock empty
    stores and an accidental request on it shows up as untouched state.
    """
    hosts: dict[str, BuildHost] = {}
    for entry in inventory:
        host = BuildHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            seed(host, entry, build_exit=build_exit)
        hosts[host.name] = host
    return hosts


async def run_role(
    inventory: list[dict[str, Any]],
    hosts: dict[str, BuildHost],
    *,
    dry: bool = False,
) -> Any:
    """Resolve the role's variables and run one wave pass against persistent hosts.

    The same ``Host`` objects come back for every entry, so state survives from
    one pass to the next, which is the whole point of the second run.
    """
    vars_set = resolve_vars(DaemonizeVars, "daemonize", inventory)

    def factory(entry: dict[str, Any]) -> BuildHost:
        return hosts[entry["host"]]

    report, _hosts = await run_waves(
        inventory, daemonize(vars_set), host_factory=factory, dry=dry
    )
    return report


@pytest.fixture
def rig() -> tuple[list[dict[str, Any]], dict[str, BuildHost]]:
    """An inventory plus the persistent simulated hosts it materializes into."""
    inventory = make_inventory()
    return inventory, make_hosts(inventory)


# --- report readers ----------------------------------------------------------


def changed_entries(report: Any) -> list[Any]:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def host_records(report: Any, host: str = "ubuntu") -> list[Any]:
    """Every record (request step or barrier) attributed to *host*."""
    return [record for record in report.records if record.host == host]


def barrier_names(report: Any, host: str = "ubuntu") -> list[str]:
    """The label of every barrier *host* parked on, in wave order.

    Mirrors what ``report.tree()`` prints: an action barrier is labelled by its
    own ``name``, a fact barrier carries none and is labelled by the fact class.
    """
    labels = []
    for record in host_records(report, host):
        barrier = record.barrier
        if barrier is None:
            continue
        labels.append(getattr(barrier, "name", "") or barrier.fact_cls.__name__)
    return labels


def wave_of(report: Any, name: str, host: str = "ubuntu") -> int:
    """The wave one request name was applied in."""
    return next(
        record.wave
        for record in host_records(report, host)
        if record.planned is not None and record.planned.request.name == name
    )


def barrier_waves(report: Any, host: str = "ubuntu") -> dict[str, int]:
    """The wave each barrier was parked in, keyed by its tree label.

    An ``act`` barrier is a record, not a planned request and not a changed log
    entry -- its ``Result`` is data -- so this is how a test locates the build.
    """
    waves = {}
    for record in host_records(report, host):
        barrier = record.barrier
        if barrier is None:
            continue
        waves[getattr(barrier, "name", "") or barrier.fact_cls.__name__] = record.wave
    return waves


def request_kwargs(report: Any, name: str, host: str = "ubuntu") -> dict:
    """The forwarded keyword arguments of the one planned request called *name*."""
    record = next(
        record
        for record in host_records(report, host)
        if record.planned is not None and record.planned.request.name == name
    )
    return record.planned.request.kwargs


# --- variables ----------------------------------------------------------------


def test_model_defaults_match_the_role_defaults():
    """``defaults/main.yml``, verbatim: workspace, version, install path."""
    hv = DaemonizeVars()

    assert (hv.version, hv.workspace, hv.install_path) == ("1.7.7", "/root", "/usr")


def test_plan_arithmetic():
    """The three derived paths are the role's own interpolations."""
    plan = resolve_daemonize(DaemonizeVars())

    assert plan.src_dir == "/root/daemonize-release-1.7.7"
    assert plan.creates == "/root/daemonize-release-1.7.7/INSTALL"
    assert plan.tarball_url == (
        "https://github.com/bmc/daemonize/archive/release-1.7.7.tar.gz"
    )


@pytest.mark.parametrize("version", ["1.7", "1.7.7.1", "1.8"])
def test_version_shape_accepts_dotted_digits(version: str):
    assert DaemonizeVars(version=version).version == version


@pytest.mark.parametrize("version", ["", "1.7.x", "v1.7.7", "1.7.7-rc1"])
def test_version_shape_rejects_anything_else(version: str):
    with pytest.raises(ValidationError):
        DaemonizeVars(version=version)


def test_workspace_must_not_be_empty():
    with pytest.raises(ValidationError):
        DaemonizeVars(workspace="")


def test_install_path_must_be_absolute():
    with pytest.raises(ValidationError):
        DaemonizeVars(install_path="usr/local")


def test_unknown_keys_are_rejected():
    """The model is ``extra="forbid"``: a typoed role-mapping key never binds."""
    with pytest.raises(ValueError):
        resolve_vars(DaemonizeVars, "daemonize", make_inventory(daemonize={"daemonize_version": "1.7.5"}))


# --- the build shell ------------------------------------------------------------


def test_build_command_renders_the_shell_key():
    """The wrapped ``sh -c`` chain is the exact ``shell_results`` seam key."""
    plan = plan_for(make_inventory()[0])

    assert build_command(plan) == (
        "sh -c 'cd /root/daemonize-release-1.7.7"
        " && ./configure --prefix=/usr && make && make install'"
    )
    assert "sudo " + build_command(plan) == BUILD_SHELL


# --- the first pass ---------------------------------------------------------------


async def test_first_pass_installs_toolchain_extracts_and_builds(rig, canned_fetch):
    inventory, hosts = rig
    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    # The toolchain prerequisite (D5) ran as an apt request, and the tarball
    # was fetched control-side, extracted into the workspace, spool cleaned.
    assert "build-essential" in host.apt_packages
    assert canned_fetch == [f"https://github.com/bmc/daemonize/archive/release-{VERSION}.tar.gz"]
    assert f"{WORKSPACE}/daemonize-release-{VERSION}" in host.directory_stats
    assert host.existing_files & {f"{WORKSPACE}/daemonize-release-{VERSION}/INSTALL"}
    assert _unarchive_spool_path(canned_fetch[0]) not in host.remote_files

    # The build ran exactly once, through the action seam, as the sudo chain.
    assert host.ran_shells == [BUILD_SHELL]

    # Both setup requests changed and are logged under the role's own task
    # names; the build is not a log entry (its Result is data), so it is
    # located through the barrier records instead.
    names = {entry.name for entry in changed_entries(report)}
    assert {TOOLCHAIN_NAME, SETUP_NAME} <= names
    assert barrier_waves(report) == {"Which": 0, BUILD_NAME: 1}


async def test_the_barrier_sequence_is_the_single_which_read(rig, canned_fetch):
    """One barrier on a fresh host, and it is the ``which`` probe (D7)."""
    inventory, hosts = rig
    report = await run_role(inventory, hosts)

    assert barrier_names(report) == ["Which", BUILD_NAME]


async def test_setup_precedes_the_probe_and_the_build_lands_after_it(rig, canned_fetch):
    """The in-stream read: wave 0's requests close on ``Which``, wave 1 builds.

    Design doc section 6: reading a barrier result and branching on it is the
    model working as intended. The source tree must be on the host before
    ``configure`` can run, so the probe sits after the setup wave applied --
    and the build lands in the wave after the probe.
    """
    inventory, hosts = rig
    report = await run_role(inventory, hosts)

    assert wave_of(report, TOOLCHAIN_NAME) == 0
    assert wave_of(report, SETUP_NAME) == 0
    assert barrier_waves(report)[BUILD_NAME] == 1

    records = host_records(report)
    probe_index = next(
        index
        for index, record in enumerate(records)
        if type(record.barrier).__name__ == "FactRequest"
    )
    setup_index = next(
        index
        for index, record in enumerate(records)
        if record.planned is not None and record.planned.request.name == SETUP_NAME
    )
    assert probe_index > setup_index, "the probe is read after the extraction is issued"
    assert records[probe_index].wave == records[setup_index].wave
    assert records[probe_index].outcome is None, "a fresh host has no daemonize on PATH"
    assert records[probe_index].barrier.kwargs == {"command": "daemonize"}


async def test_requests_forward_the_roles_own_arguments(rig, canned_fetch):
    """The unarchive request carries the URL, the workspace and the ``creates`` marker."""
    inventory, hosts = rig
    report = await run_role(inventory, hosts)

    kwargs = request_kwargs(report, SETUP_NAME)
    assert kwargs["src"] == TARBALL_URL.format(version=VERSION)
    assert kwargs["dest"] == WORKSPACE
    assert kwargs["creates"] == f"{WORKSPACE}/daemonize-release-{VERSION}/INSTALL"
    assert request_kwargs(report, TOOLCHAIN_NAME)["packages"] == ("build-essential",)


async def test_non_ubuntu_host_is_never_touched(rig, canned_fetch):
    """The family guard returns before the first yield: no records, no mutation."""
    inventory, hosts = rig
    report = await run_role(inventory, hosts)

    skipped = hosts["web1"]
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}
    assert skipped.directory_stats == {}
    assert skipped.ran_shells == []
    assert host_records(report, "web1") == []


# --- convergence ----------------------------------------------------------------


async def test_second_pass_changes_nothing_fetches_nothing_builds_nothing(rig, canned_fetch):
    """The idempotence claim: the machine, not a frozen plan, quiets the role.

    Pass 2: ``apt.packages`` reads the installed toolchain back, the
    ``creates`` marker extracted on pass 1 skips the fetch (and the fetch
    never happens, so no network), and ``Which`` now resolves the planted
    ``/usr/sbin/daemonize`` -- so the build barrier is never emitted at all and
    the host ends in wave 0.
    """
    inventory, hosts = rig
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    second = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    assert changed_entries(second) == []
    assert canned_fetch == [TARBALL_URL.format(version=VERSION)], "the creates gate skips the second fetch"
    assert host.ran_shells == [BUILD_SHELL], "the build runs exactly once"
    assert barrier_names(second) == ["Which"]
    assert wave_of(second, TOOLCHAIN_NAME) == 0
    assert wave_of(second, SETUP_NAME) == 0


async def test_preinstalled_binary_skips_the_build_on_the_first_pass(canned_fetch):
    """``when: daemonize_installed.rc != 0`` as an in-stream read.

    A host that already has ``daemonize`` on ``PATH`` never reaches the build:
    the probe answers, the body skips, one wave is all there is.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    host.command_paths["daemonize"] = "/usr/local/bin/daemonize"

    report = await run_role(inventory, hosts)

    assert barrier_names(report) == ["Which"]
    assert host.ran_shells == []
    assert wave_of(report, SETUP_NAME) == 0
    assert BUILD_NAME not in {entry.name for entry in changed_entries(report)}


async def test_dry_preview_parks_on_the_probe(rig):
    """``dry=True`` is one wave deep: the setup requests are planned, nothing applied.

    The preview stops at the ``Which`` barrier, so the build wave -- the part
    that would compile -- is never planned, which is why the role drops the
    role's ``ignore_errors: ansible_check_mode`` (D6).
    """
    inventory, hosts = rig
    host = hosts["ubuntu"]

    report = await run_role(inventory, hosts, dry=True)

    assert host.remote_files == {}
    assert host.apt_packages == {}
    assert host.ran_shells == []
    assert host.directory_stats == {WORKSPACE: {"mode": 700, "user": "root", "group": "root"}}
    tree = report.tree()
    assert TOOLCHAIN_NAME in tree
    assert SETUP_NAME in tree
    assert BUILD_NAME not in tree


# --- the build gate and its failure ----------------------------------------------


async def test_build_failure_raises_command_error(rig, canned_fetch):
    """A barrier's non-zero exit is data, so the body turns it into the error."""
    inventory, hosts = rig
    hosts["ubuntu"].shell_results[BUILD_SHELL]["exit_status"] = 2

    with pytest.raises(CommandError, match="daemonize-release"):
        await run_role(inventory, hosts)

    host = hosts["ubuntu"]
    assert host.ran_shells == [BUILD_SHELL]
    # The failed build planted nothing: the binary is still absent.
    assert "/usr/sbin/daemonize" not in host.existing_files
    assert "daemonize" not in host.command_paths


async def test_version_override_moves_the_url_and_the_paths(canned_fetch):
    """One ``version`` var moves the tag, the URL, the src dir and the gate together.

    The role interpolates ``daemonize_version`` into all four spellings; the
    plan derives them from the same field, so an override is the whole story.
    """
    inventory = make_inventory(daemonize={"version": "1.7.5"})
    hosts = make_hosts(inventory)

    report = await run_role(inventory, hosts)
    host = hosts["ubuntu"]

    assert canned_fetch == [TARBALL_URL.format(version="1.7.5")]
    kwargs = request_kwargs(report, SETUP_NAME)
    assert kwargs["src"].endswith("release-1.7.5.tar.gz")
    assert kwargs["creates"] == f"{WORKSPACE}/daemonize-release-1.7.5/INSTALL"
    assert host.ran_shells == [
        (
            "sudo sh -c 'cd /root/daemonize-release-1.7.5"
            " && ./configure --prefix=/usr && make && make install'"
        )
    ]


async def test_workspace_and_install_path_overrides_are_respected(canned_fetch):
    """``workspace=/opt`` extracts under /opt; ``install_path=/usr/local`` builds there.

    Both are also ``op()`` arguments or shell text, so this doubles as the
    hashability proof for the derived values (frozen plan, plain strings).
    """
    inventory = make_inventory(
        daemonize={"workspace": "/opt", "install_path": "/usr/local"}
    )
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    await run_role(inventory, hosts)

    assert "/opt/daemonize-release-1.7.7" in host.directory_stats
    assert host.ran_shells == [
        (
            "sudo sh -c 'cd /opt/daemonize-release-1.7.7"
            " && ./configure --prefix=/usr/local && make && make install'"
        )
    ]
    # The BuildHost planted the binary at the *overridden* prefix (the
    # Makefile's sbin installdir), and a converged pass 2 sees no change.
    assert "/usr/local/sbin/daemonize" in host.remote_files
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- tree snapshots (captured from a real probe run, never hand-written) ------

# The spool name is derived from the URL, so the fresh pass always stages to the
# same ``/tmp`` path; ``_unarchive_spool_path`` recomputes it rather than freeze a
# hash that could drift with the operation.
_SPOOL = _unarchive_spool_path(TARBALL_URL.format(version=VERSION))

_FRESH_TREE = """\
(root)
  wave 0
    ubuntu: Ensure build tools are installed. [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install build-essential
    ubuntu: Expand daemonize archive. [changed: will extract https://github.com/bmc/daemonize/archive/release-1.7.7.tar.gz into /root]
        $ scp <files unarchive> -> @@SPOOL@@
        $ sudo tar -xz -f @@SPOOL@@ -C /root
        $ sudo rm -rf @@SPOOL@@
    barrier: Which → None
  wave 1
    barrier: Build daemonize. → {'exit_status': 0, 'stdout': '', 'stderr': ''}"""

FRESH_TREE = _FRESH_TREE.replace("@@SPOOL@@", _SPOOL)

RERUN_TREE = """\
(root)
  wave 0
    ubuntu: Ensure build tools are installed. [no change: all packages present]
    ubuntu: Expand daemonize archive. [no change: archive already extracted (/root/daemonize-release-1.7.7/INSTALL exists)]
    barrier: Which → /usr/sbin/daemonize"""

DRY_TREE = """\
(root)
  wave 0
    ubuntu: Ensure build tools are installed. [changed: 1 package(s) to install]
    ubuntu: Expand daemonize archive. [changed: will extract https://github.com/bmc/daemonize/archive/release-1.7.7.tar.gz into /root]
    barrier: Which → unknown until run"""


async def test_fresh_tree_snapshot(rig, canned_fetch):
    """The whole two-wave fresh stream, exactly as ``report.tree()`` prints it."""
    inventory, hosts = rig
    report = await run_role(inventory, hosts)

    assert report.tree() == FRESH_TREE


async def test_converged_tree_snapshot(rig, canned_fetch):
    """The rerun: both setup requests report no change, the probe resolves, no wave 1.

    The build barrier line is *absent* -- that is the whole idempotence claim
    in the report's own rendering: the second pass never reaches the compile.
    """
    inventory, hosts = rig
    await run_role(inventory, hosts)
    second = await run_role(inventory, hosts)

    assert second.tree() == RERUN_TREE


async def test_dry_tree_snapshot(rig):
    """The dry preview parks on the probe: setup planned, ``Which`` unknown, no build."""
    inventory, hosts = rig
    report = await run_role(inventory, hosts, dry=True)

    assert report.tree() == DRY_TREE
