# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Offline end-to-end tests for the ``ruby`` role (§17.5).

The role is library code now, so it is imported (``from reemote.roles import
...``) and replayed against ``SimulatedHost`` through the real wave driver:
``run_waves(inventory, ruby(v), host_factory=...)``. The factory is plain
(non-``async``) -- the one barrier it yields needs no factory IO -- so it is
called, not awaited.

The load-bearing assertions are the migration's shape claim and its two claims:
**one ``FileExists`` barrier and up to two request waves** (wave 0 provisions
apt + the profile script, the barrier reads for ``gem``, wave 1 is the gem
loop); *convergence* (run the role to a settled host state, then run it again
and the second pass reports no change anywhere); and the family guard (a host
outside ``"Ubuntu"`` records nothing).

Because there is a barrier, ``dry=True`` parks the way the pip test observes:
it plans wave 0, then stops at ``barrier: FileExists → unknown until run``
without reading anything.

One simulation bridge is needed (D9's offline face): ``install_apt_packages``
records package *names* only -- the simulation materializes no payload files --
so the ``/usr/bin/gem`` the barrier probes on a real host after wave 0 has to be
seeded between passes (``host.existing_files.add(GEM_BIN)``). The converged
seeds therefore include the binary by default; the fresh-provisioning pass
excludes it with ``gem_bin_present=False`` and asserts exactly what pass 1 does
before the bridge: apt + script, with the gem loop deferred (the live double-run
stands in for the bridge).
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from reemote import run_waves
from reemote.modules import files
from reemote.roles import GemSpec, RubyVars, ruby
from reemote.roles.ruby import (
    APT_CACHE_VALID_TIME,
    DEFAULT_RUBYGEMS_PACKAGE,
    GEM_BIN,
    RUBY_PACKAGES,
    RUBYGEMS_PROFILE_PATH,
    RUBYGEMS_PROFILE_SH,
    gem_spec,
)
from reemote.rolevars import RoleVarsError, resolve_vars
from reemote.stages import CheckpointRequest, FactRequest
from tests.simulated_host import SimulatedHost

# The Ubuntu target's install_gems, the same shape the inventory carries as a
# ``ruby_install_gems`` key: a version-pinned system gem and a bare user-scope
# gem (the Ansible module's default scope, D6/D7).
INSTALL_GEMS = [
    {"name": "rake", "version": "13.2.1", "user_install": False},
    "colorize",
]

# The role's requests, in stream order (D6: one request per loop item).
REQUEST_NAMES = (
    "Install ruby and other required dependencies.",
    "Install rubygems.",
    "Add user installed RubyGems bin directory to global $PATH.",
    "Install Bundler.",
    "Ensure gem rake is installed.",
    "Ensure gem colorize is installed.",
)

# The first three are wave 0, the rest ride the GEM_BIN barrier into wave 1 (D9).
WAVE0_NAMES = REQUEST_NAMES[:3]


def _regular_file(host: SimulatedHost, path: str, data: bytes = b"", mode: int = 644) -> None:
    """Seed an existing regular file: content, existence and stat metadata."""
    host.remote_files[path] = data
    host.existing_files.add(path)
    host.file_stats[path] = {
        "user": "root",
        "group": "root",
        "mode": mode,
        "size": len(data),
    }


def make_inventory(**overrides: Any) -> list[dict[str, Any]]:
    """A two-host inventory: one Ubuntu target, one RedHat host to be skipped.

    The role variables ride on the Ubuntu entry's ``roles`` mapping under
    ``"ruby"``, written with the model's bare field names, which is what
    ``resolve_vars(RubyVars, "ruby", inventory)`` binds, so a test changes a
    variable exactly the way an operator does.
    """
    entry: dict[str, Any] = {
        "host": "web1",
        "family": "Ubuntu",
        "connection": {
            "address": "192.0.2.10",
            "username": "supervisor",
            "key_path": "~/.ssh/id_ed25519",
        },
        "group": "ubuntu",
        "roles": {
            "ruby": {
                "install_gems": INSTALL_GEMS,
            },
        },
    }
    entry["roles"]["ruby"].update(overrides)
    return [
        entry,
        {
            "host": "centos",
            "family": "RedHat",
            "connection": {"address": "192.0.2.12", "username": "admin"},
            "group": "centos",
        },
    ]


def make_hosts(
    inventory: list[dict[str, Any]],
    *,
    packages_installed: bool = False,
    cache_fresh: bool = False,
    script_present: bool = False,
    gems_installed: bool = False,
    gem_bin_present: bool = True,
) -> dict[str, SimulatedHost]:
    """Materialize the inventory into persistent simulated hosts, seeded targets.

    Only the Ubuntu target gets the ruby seeding: the RedHat entry keeps its
    stock simulated state, so a role that reached it would be caught by the
    untouched-store assertions. The apt store, the cache stamp, the profile
    script and the scoped gem store seed the converged state the rerun must
    observe; ``gem_bin_present`` seeds (or withholds) the ``/usr/bin/gem`` the
    D9 barrier reads -- the apt mutators create no payload files, so a
    provisioning pass withholds it and the test bridges it by hand.
    """
    hosts: dict[str, SimulatedHost] = {}
    for entry in inventory:
        host = SimulatedHost.from_inventory(entry)
        if entry.get("family") == "Ubuntu":
            if gem_bin_present:
                host.existing_files.add(GEM_BIN)
            if packages_installed:
                for name in (*RUBY_PACKAGES, DEFAULT_RUBYGEMS_PACKAGE):
                    host.apt_packages[name] = [""]
            if cache_fresh:
                host.apt_update_stamp = datetime.now(UTC).replace(tzinfo=None)
            if script_present:
                _regular_file(host, RUBYGEMS_PROFILE_PATH, RUBYGEMS_PROFILE_SH.encode())
            if gems_installed:
                host.gem_packages = {
                    "system": {"bundler": ["2.5.23"], "rake": ["13.2.1"]},
                    "user-install": {"colorize": ["1.1.0"]},
                }
        hosts[host.name] = host
    return hosts


async def run_role(
    inventory: list[dict[str, Any]],
    hosts: dict[str, SimulatedHost],
    *,
    dry: bool = False,
) -> Any:
    """Resolve the role's variables and run one wave pass against persistent hosts."""
    v = resolve_vars(RubyVars, "ruby", inventory)

    def factory(entry: dict[str, Any]) -> SimulatedHost:
        return hosts[entry["host"]]

    report, _hosts = await run_waves(
        inventory, ruby(v), host_factory=factory, dry=dry
    )
    return report


def changed_entries(report: Any) -> list[Any]:
    """Every changed log entry of one pass."""
    return [entry for entry in report.merged().log if entry.changed]


def entry_named(report: Any, name: str, host: str = "web1") -> Any:
    """The one log entry with *name* on *host*."""
    return next(
        entry for entry in report.merged().log if entry.name == name and entry.host == host
    )


def host_records(report: Any, host: str) -> list[Any]:
    """Every record (request step or barrier) attributed to *host*."""
    return [record for record in report.records if record.host == host]


def _kind(barrier: Any) -> tuple:
    """Classify one barrier record so a speculatively-added barrier shows up as
    a distinct label and breaks the shape test rather than hiding (the ``git``
    harness precedent)."""
    if isinstance(barrier, FactRequest):
        return ("fact", barrier.fact_cls)
    if isinstance(barrier, CheckpointRequest):
        return ("checkpoint", barrier.name)
    return ("other", type(barrier).__name__)


def barrier_shape(report: Any) -> list:
    """The web1 host's ordered ``(wave, kind)`` barrier list for one pass."""
    return [
        (r.wave, _kind(r.barrier))
        for r in host_records(report, "web1")
        if r.barrier is not None
    ]


def request_waves(report: Any) -> set[int]:
    """The wave numbers that carried at least one applied web1 request."""
    return {r.wave for r in host_records(report, "web1") if r.log is not None}


def planned_waves(report: Any) -> set[int]:
    """The wave numbers that carried at least one *planned* web1 request.

    Dry mode plans but never applies, so ``log`` is ``None`` while ``planned``
    is set -- the dry analogue of :func:`request_waves`.
    """
    return {
        r.wave for r in host_records(report, "web1") if r.planned is not None
    }


# --- the stream shape (the D9 barrier) -----------------------------------------


async def test_one_barrier_splits_the_gem_loop_into_a_second_wave():
    """The shape claim: wave 0 provisions, the barrier gates the gem loop."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    assert barrier_shape(report) == [(0, ("fact", files.FileExists))]
    assert request_waves(report) == {0, 1}

    ordered = [entry.name for entry in report.merged().log if entry.host == "web1"]
    assert ordered == list(REQUEST_NAMES)

    second = await run_role(inventory, hosts)
    assert barrier_shape(second) == [(0, ("fact", files.FileExists))]
    assert request_waves(second) == {0, 1}


async def test_missing_gem_bin_defers_the_loop_to_the_next_invocation():
    """D9 offline face: barrier ``no`` -> wave 0 only, and nothing is dropped.

    The simulation's apt mutators create no payload files, so a truly fresh
    host's first pass ends at the barrier; bridging ``/usr/bin/gem`` (what the
    live apt install does) lets the next invocation run the loop.
    """
    inventory = make_inventory()
    hosts = make_hosts(inventory, gem_bin_present=False)
    host = hosts["web1"]

    first = await run_role(inventory, hosts)
    assert [entry.name for entry in first.merged().log if entry.host == "web1"] == (
        list(WAVE0_NAMES)
    )
    assert barrier_shape(first) == [(0, ("fact", files.FileExists))]
    assert request_waves(first) == {0}
    assert host.gem_packages == {}
    # Wave 0 still landed everything it owned.
    for name in (*RUBY_PACKAGES, DEFAULT_RUBYGEMS_PACKAGE):
        assert name in host.apt_packages, name
    assert host.remote_files[RUBYGEMS_PROFILE_PATH] == RUBYGEMS_PROFILE_SH.encode()

    # The bridge the live driver gets for free: apt has now shipped the binary.
    host.existing_files.add(GEM_BIN)
    second = await run_role(inventory, hosts)
    gem_names = [entry.name for entry in second.merged().log if entry.host == "web1"]
    assert gem_names == list(REQUEST_NAMES)
    assert request_waves(second) == {0, 1}
    assert changed_entries(second), "the deferred loop must actually run once unblocked"
    assert "bundler" in host.gem_packages["system"]


# --- the first pass really installs ruby ---------------------------------------


async def test_first_pass_installs_ruby():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    host = hosts["web1"]
    report = await run_role(inventory, hosts)

    # Every request ran and reported a change on a fresh host.
    for name in REQUEST_NAMES:
        assert entry_named(report, name).changed, name

    # The apt packages, the profile script and both gem scopes all landed.
    for name in (*RUBY_PACKAGES, DEFAULT_RUBYGEMS_PACKAGE):
        assert name in host.apt_packages, name
    assert host.remote_files[RUBYGEMS_PROFILE_PATH] == RUBYGEMS_PROFILE_SH.encode()
    system = host.gem_packages["system"]
    assert "bundler" in system
    assert system["rake"] == ["13.2.1"]  # the pin rode the install token
    assert "colorize" in host.gem_packages["user-install"]


async def test_non_ubuntu_host_emits_nothing():
    """The family guard is the body's opening ``if``, not a ``when`` on each op."""
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    skipped = hosts["centos"]
    assert host_records(report, "centos") == []
    assert [e for e in report.merged().log if e.host == "centos"] == []
    assert skipped.gem_packages == {}
    assert skipped.apt_packages.get("ruby-full") is None
    assert RUBYGEMS_PROFILE_PATH not in skipped.remote_files


# --- the second pass is a no-op -------------------------------------------------


async def test_second_pass_changes_nothing():
    inventory = make_inventory()
    hosts = make_hosts(inventory)
    first = await run_role(inventory, hosts)
    assert changed_entries(first), "the first pass must actually deploy something"

    second = await run_role(inventory, hosts)
    assert changed_entries(second) == []


async def test_converged_host_is_a_full_noop():
    """Packages + fresh cache + script + gems all already in place."""
    inventory = make_inventory()
    hosts = make_hosts(
        inventory,
        packages_installed=True,
        cache_fresh=True,
        script_present=True,
        gems_installed=True,
    )
    report = await run_role(inventory, hosts)

    assert changed_entries(report) == []
    assert [e.name for e in report.merged().log if e.host == "web1"] == list(
        REQUEST_NAMES
    )


async def test_dry_preview_parks_at_the_barrier():
    """``dry`` stops at the first barrier: wave 0 planned, gem loop unseen."""
    inventory = make_inventory()
    hosts = make_hosts(inventory, gem_bin_present=False)
    host = hosts["web1"]

    report = await run_role(inventory, hosts, dry=True)

    assert barrier_shape(report) == [(0, ("fact", files.FileExists))]
    assert planned_waves(report) == {0}
    assert request_waves(report) == set()
    assert "barrier: FileExists → unknown until run" in report.tree()
    assert changed_entries(report) == []
    assert host.apt_packages.get("ruby-full") is None
    assert host.gem_packages == {}
    assert RUBYGEMS_PROFILE_PATH not in host.remote_files


# --- the gem loop's per-item knobs ----------------------------------------------


async def test_bundler_gate_skips_the_request():
    """``install_bundler: false`` drops the Bundler line from the stream."""
    inventory = make_inventory(install_bundler=False)
    hosts = make_hosts(inventory, gems_installed=False)
    report = await run_role(inventory, hosts)

    names = [entry.name for entry in report.merged().log if entry.host == "web1"]
    assert "Install Bundler." not in names
    assert "bundler" not in hosts["web1"].gem_packages.get("system", {})


async def test_gem_scope_selects_flags_and_privilege():
    """D6/D7: user_install drives the scope flag; privilege follows the scope."""
    inventory = make_inventory(
        install_bundler=False,
        install_gems=[
            {"name": "sass", "user_install": False},
            {"name": "colorize", "bindir": "/usr/local/bin"},
        ],
    )
    hosts = make_hosts(inventory)
    report = await run_role(inventory, hosts)

    system = entry_named(report, "Ensure gem sass is installed.")
    user = entry_named(report, "Ensure gem colorize is installed.")
    assert system.changed and user.changed

    # The system install elevates and names gem's --no-user-install flag; the
    # user install rides the login connection and --user-install, and renders
    # its bindir knob.
    system_shell = system.commands[0].shell()
    user_shell = user.commands[0].shell()
    assert "gem install --no-user-install sass" == system_shell
    assert user_shell == "gem install --user-install --bindir /usr/local/bin colorize"
    assert system.sudo is True
    assert user.sudo is False
    # Each scope mutated its own store.
    assert "sass" in hosts["web1"].gem_packages["system"]
    assert "colorize" in hosts["web1"].gem_packages["user-install"]


async def test_pinned_gem_at_another_version_reinstalls():
    """The pin is exact: rake 13.2.1 requested against 13.0.6 is a change."""
    inventory = make_inventory(
        install_bundler=False,
        install_gems=[{"name": "rake", "version": "13.2.1", "user_install": False}],
    )
    hosts = make_hosts(inventory)
    hosts["web1"].gem_packages = {"system": {"rake": ["13.0.6"]}}

    report = await run_role(inventory, hosts)

    assert entry_named(report, "Ensure gem rake is installed.").changed
    assert sorted(hosts["web1"].gem_packages["system"]["rake"]) == ["13.0.6", "13.2.1"]

    # With the exact version present, the same request is a no-op.
    second = await run_role(inventory, hosts)
    assert not entry_named(second, "Ensure gem rake is installed.").changed


async def test_profile_script_content_only_idempotence():
    """D5: a differing script re-installs; the exact bytes do not."""
    inventory = make_inventory(install_bundler=False, install_gems=[])
    hosts = make_hosts(inventory, packages_installed=True, cache_fresh=True)
    host = hosts["web1"]
    _regular_file(host, RUBYGEMS_PROFILE_PATH, b"# stale\n")

    report = await run_role(inventory, hosts)
    assert entry_named(report, "Add user installed RubyGems bin directory to global $PATH.").changed
    assert host.remote_files[RUBYGEMS_PROFILE_PATH] == RUBYGEMS_PROFILE_SH.encode()

    second = await run_role(inventory, hosts)
    assert not entry_named(second, "Add user installed RubyGems bin directory to global $PATH.").changed


# --- the pure half, unit-tested directly -----------------------------------------


def test_gem_spec():
    assert gem_spec(GemSpec(name="rails")) == "rails"
    assert gem_spec(GemSpec(name="rake", version="13.2.1")) == "rake=13.2.1"


def test_model_defaults_are_the_ubuntu_path():
    hv = RubyVars()

    assert hv.packages == RUBY_PACKAGES
    assert hv.rubygems_package == "ruby-rubygems"  # D3
    assert hv.install_bundler is True
    assert hv.install_gems == ()
    assert hv.install_from_source is False
    assert hash(hv)  # frozen and hashable, as a VarsSet payload must be
    assert APT_CACHE_VALID_TIME == 86400


def test_bare_string_item_is_coerced_to_a_name():
    v = RubyVars.model_validate({"install_gems": ["colorize", {"name": "rake"}]})

    assert v.install_gems == (
        GemSpec(name="colorize"),
        GemSpec(name="rake"),
    )
    # The Ansible gem module's user_install default carries into the model (D6).
    assert v.install_gems[0].user_install is True


def test_inventory_binds_the_prefixed_variables():
    inventory = make_inventory(packages=["ruby-full"], rubygems_package="rubygems")
    v = resolve_vars(RubyVars, "ruby", inventory)

    assert v["web1"].packages == ("ruby-full",)
    assert v["web1"].rubygems_package == "rubygems"  # the field keeps the override channel
    # The other role's host keeps the defaults: no ruby keys, nothing bound.
    assert v["centos"] == RubyVars()


def test_vars_reject_bad_values():
    with pytest.raises(ValidationError, match="non-empty"):
        RubyVars(rubygems_package="")
    with pytest.raises(ValidationError, match="non-empty"):
        RubyVars(packages=["ruby-full", ""])
    with pytest.raises(ValidationError, match="non-empty"):
        GemSpec(name="")
    with pytest.raises(ValidationError, match="non-empty"):
        GemSpec(name="rake", version="")
    with pytest.raises(ValidationError, match="non-empty"):
        GemSpec(name="rake", bindir="")


def test_vars_are_frozen_and_reject_unknown_fields():
    v = RubyVars()
    with pytest.raises(ValidationError):
        v.install_bundler = False  # type: ignore[misc]
    with pytest.raises(ValidationError):
        RubyVars(ruby_packages=list(RUBY_PACKAGES))  # the Ansible name, not the field


def test_install_from_source_fails_at_resolution():
    """D8: the scoped-out source branch is rejected before any host contact."""
    inventory = make_inventory(install_from_source=True)
    with pytest.raises(ValueError, match="scoped out"):
        resolve_vars(RubyVars, "ruby", inventory)


def test_source_knobs_are_not_fields():
    """D8: ``ruby_version``/``workspace`` and friends are not variables to bind.

    A role mapping that sets one is a spelling mistake against this
    migration's contract, so ``RoleVarsError`` names the valid fields instead
    of silently ignoring the knob.
    """
    for key in ("version", "download_url", "workspace", "gems_user"):
        inventory = make_inventory()
        inventory[0]["roles"]["ruby"][key] = "whatever"
        with pytest.raises(RoleVarsError, match=key):
            resolve_vars(RubyVars, "ruby", inventory)


# --- the wave tree (design doc section 7.2) --------------------------------------

# Pinned from real simulated runs. The provisioning pass ends at the barrier's
# ``False`` (the apt mutators ship no payload file); after the bridge the loop
# runs in wave 1; the converged rerun reports no change anywhere. The RedHat
# host never appears: no barrier record, no request line.
PROVISION_TREE = """(root)
  wave 0
    web1: Install ruby and other required dependencies. [changed: apt update; 6 package(s) to install]
        $ sudo apt-get update
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install ruby-full ruby-dev build-essential autogen autoconf libtool
    web1: Install rubygems.        [changed: 1 package(s) to install]
        $ sudo DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" install ruby-rubygems
    web1: Add user installed RubyGems bin directory to global $PATH. [changed: content /etc/profile.d/rubygems.sh is absent, will install]
        $ scp <files install> -> /tmp/reemote-install-df1e80eae4cae341/content
        $ sudo sh -c 'mkdir -p /etc/profile.d && cp /tmp/reemote-install-df1e80eae4cae341/content /etc/profile.d/rubygems.sh && chmod 644 /etc/profile.d/rubygems.sh && rm -rf /tmp/reemote-install-df1e80eae4cae341'
    barrier: FileExists → False"""

GEM_PASS_TREE = """(root)
  wave 0
    web1: Install ruby and other required dependencies. [no change: apt update skipped, cache is fresh; all packages present]
    web1: Install rubygems.        [no change: all packages present]
    web1: Add user installed RubyGems bin directory to global $PATH. [no change: content already up to date (/etc/profile.d/rubygems.sh)]
    barrier: FileExists → True
  wave 1
    web1: Install Bundler.         [changed: 1 package(s) to install]
        $ sudo gem install --no-user-install bundler
    web1: Ensure gem rake is installed. [changed: 1 package(s) to install]
        $ sudo gem install --no-user-install rake:13.2.1
    web1: Ensure gem colorize is installed. [changed: 1 package(s) to install]
        $ gem install --user-install colorize"""

CONVERGED_TREE = """(root)
  wave 0
    web1: Install ruby and other required dependencies. [no change: apt update skipped, cache is fresh; all packages present]
    web1: Install rubygems.        [no change: all packages present]
    web1: Add user installed RubyGems bin directory to global $PATH. [no change: content already up to date (/etc/profile.d/rubygems.sh)]
    barrier: FileExists → True
  wave 1
    web1: Install Bundler.         [no change: all packages present]
    web1: Ensure gem rake is installed. [no change: all packages present]
    web1: Ensure gem colorize is installed. [no change: all packages present]"""


async def test_tree_snapshot():
    """The run as trees: provision, bridge + gem loop, then the converged rerun."""
    inventory = make_inventory()
    hosts = make_hosts(inventory, gem_bin_present=False)

    first = await run_role(inventory, hosts)
    assert first.tree() == PROVISION_TREE

    hosts["web1"].existing_files.add(GEM_BIN)
    second = await run_role(inventory, hosts)
    assert second.tree() == GEM_PASS_TREE

    third = await run_role(inventory, hosts)
    assert third.tree() == CONVERGED_TREE
