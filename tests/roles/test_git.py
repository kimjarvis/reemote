# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``git`` role (§17.5, §18.12).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, git(v), host_factory=...)``. The factory is plain
(non-``async``) -- there is no barrier, so no factory IO either -- so it is
called, not awaited.

The load-bearing assertion is the migration's shape claim: **zero barriers and
one request wave** -- nothing needs a read of the machine before the single
install request can be built, so the whole role is one ``apt.packages`` record
in wave 0 and no ``fact``/``act``/``checkpoint``/``control`` record exists. A
speculatively-added barrier breaks the shape test rather than hiding.

Because there is no barrier, ``dry=True`` does not park early the way the
java/docker/repo_epel tests observe: it plans the whole wave -- reading the
installed-packages and cache-stamp facts -- and applies nothing. Dry records
carry ``planned`` but never ``log``, so the dry assertions filter on
``r.planned is not None``.

No simulation bridge is needed for idempotence: the simulation's own mutators
maintain every seam the second pass reads -- ``install_apt_packages`` records
the packages and the ``AptUpdateCommand`` arm refreshes ``apt_update_stamp``,
so the folded cache refresh (deviation D1) skips on the second apply.
"""

import pytest

from reemote import run_waves
from reemote.roles import GitVars, git
from reemote.roles.git import APT_CACHE_VALID_TIME
from reemote.rolevars import RoleVarsError, resolve_vars
from reemote.stages import CheckpointRequest, FactRequest
from tests.simulated_host import SimulatedHost


def make_inventory(**overrides) -> list[dict]:
    """A two-host inventory: one Ubuntu target, one CentOS host that must be skipped.

    The role variables ride in the entry's ``roles`` mapping under ``"git"``,
    bound by ``resolve_vars``. The base entry widens ``packages`` to
    ``git``/``git-svn`` so the default path exercises the package-list variable;
    the overrides drive the non-default branches (the empty list, the defaults,
    the scoped-out source knob).
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
            "git": {
                "packages": ["git", "git-svn"],
            },
        },
    }
    entry["roles"]["git"].update(overrides)
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
    """Pre-state: a stock minimal Ubuntu machine with no git installed.

    No apt packages and an unrefreshed cache (the folded update runs), so the
    single request is a real change on the first pass.
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
    v = resolve_vars(GitVars, "git", inventory)

    def factory(entry: dict) -> SimulatedHost:
        # The *same* host object every pass, so state survives the run.
        return hosts[entry["host"]]

    # A plain (non-async) factory: called, not awaited.
    deploy = git(v)
    report, _hosts = await run_waves(inventory, deploy, host_factory=factory, dry=dry)
    return report


def changed_entries(report) -> list:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


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
    is set -- this is the dry-run analogue of ``_request_waves``.
    """
    return {
        r.wave for r in report.records if r.host == "ubuntu" and r.planned is not None
    }


# --- the stream shape (the barrier-free falsifier) -----------------------------


async def test_stream_is_barrier_free_and_one_request_wave():
    """The shape claim: no barrier at all, the single request in wave 0.

    Nothing needs a read of the machine before the request can be built, so
    there is no ``fact``/``act``/``checkpoint``/``control`` record and the
    install lands in the single wave 0. The rerun re-derives the same shape.
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


async def test_first_pass_installs_git_and_refreshes_the_cache():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts)

    # Both packages from the role mapping are installed.
    assert "git" in host.apt_packages
    assert "git-svn" in host.apt_packages

    # The folded cache refresh ran (the stamp was absent).
    assert host.apt_update_stamp is not None

    # The single request changed on a virgin host; the role is all operations.
    log = ubuntu_log(report)
    assert log["Ensure git is installed"].changed
    assert host.ran_shells == []


async def test_second_pass_is_a_noop():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    # The migration's idempotence claim, with no bridge: the packages are
    # installed and the cache stamp is fresh, so the folded refresh skips.
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []
    assert hosts["ubuntu"].ran_shells == []


async def test_non_ubuntu_host_is_never_touched():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)
    skipped = hosts["web1"]

    # The family guard returns before the first yield, so the off-target host
    # is in no wave, emits no log line, and mutates nothing.
    assert not any(entry.host == "web1" for entry in report.merged().log)
    assert not any(r.host == "web1" for r in report.records)
    assert skipped.remote_files == {}
    assert skipped.apt_packages == {}
    assert skipped.ran_shells == []


async def test_dry_preview_plans_the_whole_wave():
    """``dry=True`` has no barrier to park on, so it plans the single request.

    Unlike java's dry stub, the preview carries the planned install -- with its
    real change reason, because planning reads facts -- but applies nothing.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]
    report = await run_role(inventory, hosts, dry=True)

    assert _barrier_shape(report) == []
    assert _planned_waves(report) == {0}
    assert _request_waves(report) == set()
    assert changed_entries(report) == []
    assert host.apt_packages == {}
    assert host.apt_update_stamp is None


# --- the override and default branches ------------------------------------------


async def test_default_packages_install_only_git():
    """No role mapping at all: the model default is the bare ``git`` install."""
    inventory = make_inventory()
    del inventory[0]["roles"]
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    await run_role(inventory, hosts)

    assert "git" in host.apt_packages
    assert "git-svn" not in host.apt_packages


async def test_empty_packages_still_refreshes_the_cache():
    """git_packages=[] is a valid Ansible override: apt installs nothing."""
    inventory = make_inventory(packages=[])
    hosts = make_hosts(inventory)
    host = hosts["ubuntu"]

    await run_role(inventory, hosts)

    assert host.apt_packages == {}
    # The folded cache refresh still runs (it is gated on the stamp, not on
    # the package list), and the second pass skips it on the fresh stamp.
    assert host.apt_update_stamp is not None
    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


# --- the fail-fast gates, through resolve_vars -----------------------------------


def test_install_from_source_fails_at_resolution():
    """D2: the scoped-out source branch is rejected before any host contact."""
    inventory = make_inventory(install_from_source=True)
    with pytest.raises(ValueError, match="scoped out"):
        resolve_vars(GitVars, "git", inventory)


def test_redhat_and_source_knobs_are_not_fields():
    """D3: ``enablerepo`` and the source sub-knobs are not variables to bind.

    A role mapping that sets one is a spelling mistake against this
    migration's contract, so ``RoleVarsError`` names the valid fields instead
    of silently ignoring the knob.
    """
    for key in ("enablerepo", "version", "workspace"):
        inventory = make_inventory()
        inventory[0]["roles"]["git"][key] = "whatever"
        with pytest.raises(RoleVarsError, match=key):
            resolve_vars(GitVars, "git", inventory)


# --- tree snapshots (the migration's published shape) ----------------------------


def test_tree_snapshots():
    """Pin the three trees the docs page transcribes: first, converged, dry."""

    async def trees():
        inventory = make_inventory()
        hosts = make_hosts(inventory)
        first = await run_role(inventory, hosts)
        converged = await run_role(inventory, hosts)
        dry_inventory = make_inventory()
        dry = await run_role(
            dry_inventory, make_hosts(dry_inventory), dry=True
        )
        return first.tree(), converged.tree(), dry.tree()

    import asyncio

    first, converged, dry = asyncio.run(trees())
    assert first == (
        "(root)\n"
        "  wave 0\n"
        "    ubuntu: Ensure git is installed  "
        "[changed: apt update; 2 package(s) to install]\n"
        "        $ sudo apt-get update\n"
        "        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y "
        '-o Dpkg::Options::="--force-confdef" -o '
        'Dpkg::Options::="--force-confold" install git git-svn'
    )
    assert converged == (
        "(root)\n"
        "  wave 0\n"
        "    ubuntu: Ensure git is installed  "
        "[no change: apt update skipped, cache is fresh; all packages present]"
    )
    # Dry plans with the real change verdict and shows no command lines.
    assert dry == (
        "(root)\n"
        "  wave 0\n"
        "    ubuntu: Ensure git is installed  "
        "[changed: apt update; 2 package(s) to install]"
    )


# --- the variables model ----------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "error", "match"),
    [
        ({"packages": "git"}, ValueError, "packages"),
        ({"packages": [42]}, ValueError, "packages"),
        ({"packages": [""]}, ValueError, "packages"),
        ({"install_from_source": True}, ValueError, "scoped out"),
        ({"unknown": "x"}, ValueError, "unknown"),  # extra="forbid"
    ],
)
def test_invalid_role_values_fail_fast(overrides, error, match):
    with pytest.raises(error, match=match):
        GitVars.model_validate(overrides)


def test_model_defaults_normalizes_and_stays_frozen():
    hv = GitVars()
    assert hv.packages == ("git",)  # the role's git_packages default
    assert hv.install_from_source is False

    normalized = GitVars.model_validate({"packages": ["git", "git-svn"]})
    assert normalized.packages == ("git", "git-svn")  # a list becomes a tuple

    assert hash(normalized) is not None
    with pytest.raises(ValueError):  # pydantic frozen-instance error
        normalized.packages = ("git",)


def test_inventory_role_variables_bind():
    """The nested mapping binds bare names; APT_CACHE_VALID_TIME is the role's."""
    inventory = make_inventory()
    v = resolve_vars(GitVars, "git", inventory)
    hv = v.for_host(SimulatedHost.from_inventory(inventory[0]))
    assert hv.packages == ("git", "git-svn")
    assert APT_CACHE_VALID_TIME == 86400
