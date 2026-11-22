# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the gem module: the ``GemPackages`` fact and ``gem.packages``.

Covers ``reemote.modules.gem.gem.GemPackages`` -- the installed RubyGems
packages as ``GemPackageInfo`` entries -- both over the real-transport path (a
``CannedShellHost`` returning canned ``gem list --local`` output, including the
shapes a live gem prints that pyinfra's ``[0-9.]+`` regex silently drops: a
multi-version row, a ``default:`` annotation, a platform tag and a name
carrying a ``.``) and through the ``SimulatedHost`` seam, plus the
``user_install``/``gem_home``/``gem_path``/``gem_bin`` arguments threading the
gem scope and the ``gather_fact`` fan-out.

The ``gem.packages`` operation -- a pyinfra port reading ``GemPackages``,
extended to the surface the Ansible ``gem`` module needs (issue #57) -- is
exercised over its three command descriptors (``GemInstallCommand`` /
``GemUpdateCommand`` / ``GemRemoveCommand``), their rendering (``gem install``/
``gem update``/``gem uninstall`` with the deterministic ``--[no-]user-install``
scope flag, the non-interactive ``-a``/``--version``/``-x``/``-I`` removal
flags and the ``GEM_HOME``/``GEM_PATH`` command environment), elevation and
shell quoting, the version-aware idempotence (exact ``=`` pins translated to
gem's ``:`` install token and to ``--version`` on removal), the ``latest``
upgrade path, removal subsumption of a pin by a bare name, the
``user_install``/``gem_home`` scope validation, the ``SimulatedHost`` seam
dispatch (per-scope stores holding a version list per name), and end to end
through ``run`` / ``run_prepare`` + ``run_apply`` (sudo elevation, per-step
fresh-cache no-ops, and a dry run).

``asyncio_mode = "auto"`` lets the async tests run without decorators.
"""

import shlex

import pytest

from reemote.core import FactCache, OpContext, op
from reemote.drivers import (
    command_shell,
    format_log,
    gather_fact,
    run,
    run_apply,
    run_prepare,
    send_command,
)
from reemote.hosts import Host
from reemote.modules import gem
from reemote.modules.gem.gem import (
    GemInstallCommand,
    GemPackageInfo,
    GemPackages,
    GemRemoveCommand,
    GemUpdateCommand,
    _gem_env,
    _gem_package_spec,
    _gem_split_spec,
    _scope_key,
    _split_version,
)
from tests.simulated_host import SimulatedHost


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell``, recording the shell it was
    given so the command parity checks can assert on it."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout
        self.last_shell: str | None = None

    async def run_shell(self, shell: str) -> str:
        self.last_shell = shell
        return self._stdout


def _entry(name: str, version: str) -> GemPackageInfo:
    """One installed ``GemPackageInfo`` entry, spelled once per test."""
    return {"name": name, "version": version}


# --- Shared helpers -----------------------------------------------------------


def test_split_version_splits_at_the_last_equals():
    """reemote's one user-facing pin spelling, shared with apt/dnf/apk/npm:
    the split is at the *last* ``=``, so a version carrying one stays whole."""
    assert _split_version("rails") == ("rails", None)
    assert _split_version("rails=7.1.0") == ("rails", "7.1.0")
    assert _split_version("odd=1=2") == ("odd=1", "2")


def test_gem_package_spec_translates_the_pin_to_gem_grammar():
    """The request's ``=`` becomes gem's ``:`` (pyinfra's ``version_join``);
    an unpinned name passes through verbatim."""
    assert _gem_package_spec("rails=7.1.0") == "rails:7.1.0"
    assert _gem_package_spec("rspec") == "rspec"


def test_gem_split_spec_inverts_the_translation():
    """The seam's reader splits at the last ``:`` -- a gem name never carries
    one, so the split is unambiguous and a bare name yields no version."""
    assert _gem_split_spec("rails:7.1.0") == ("rails", "7.1.0")
    assert _gem_split_spec("rspec") == ("rspec", None)


def test_gem_env_renders_the_command_environment():
    """The two variables the ``gem`` CLI reads, each ``shlex``-quoted; nothing
    precedes ``gem`` when neither is asked for."""
    assert _gem_env(None, None) == ""
    assert _gem_env("/opt/gems", None) == "GEM_HOME=/opt/gems "
    assert _gem_env(None, "/opt/gems") == "GEM_PATH=/opt/gems "
    assert _gem_env("/opt/gems", "/usr/share") == (
        "GEM_HOME=/opt/gems GEM_PATH=/usr/share "
    )
    hostile = "/opt/gems; rm -rf /"
    assert _gem_env(hostile, None) == f"GEM_HOME={shlex.quote(hostile)} "


def test_scope_key_names_the_gem_store():
    """One key per tree gem keeps -- the system ``GEM_HOME``, the invoking
    user's directory (``--user-install``) and each relocated override -- so the
    fact seam and the mutators address the same store; an explicit override
    wins over the user flag."""
    assert _scope_key() == "system"
    assert _scope_key(user_install=True) == "user-install"
    assert _scope_key(gem_home="/opt/gems") == "gem_home=/opt/gems"
    assert _scope_key(gem_path="/opt/gems") == "gem_path=/opt/gems"
    assert _scope_key(user_install=True, gem_home="/opt") == "gem_home=/opt"


# --- Fact: command parity -----------------------------------------------------


def test_gem_packages_command_is_the_local_listing():
    """``gem list --local``, pyinfra's own probe: the locally installed gems,
    not the remote index."""
    assert GemPackages.command() == "gem list --local"


def test_gem_packages_command_pins_the_user_store():
    """``gem list`` has no ``--user-install`` flag, so the user scope is
    addressed the way gem exposes it: ``gem env user_gemhome`` pinned into both
    ``GEM_HOME`` and ``GEM_PATH``, so the listing reads exactly the tree a
    ``--user-install`` command writes."""
    assert GemPackages.command(user_install=True) == (
        'GEM_HOME="$(gem env user_gemhome)" '
        'GEM_PATH="$(gem env user_gemhome)" gem list --local'
    )


def test_gem_packages_command_threads_the_overrides():
    """An explicit ``GEM_HOME``/``GEM_PATH`` relocates the whole store, user
    flag or not, and renders as the assignments gem reads."""
    assert GemPackages.command(gem_home="/opt/gems") == (
        "GEM_HOME=/opt/gems gem list --local"
    )
    assert GemPackages.command(gem_home="/opt/gems", gem_path="/usr/share") == (
        "GEM_HOME=/opt/gems GEM_PATH=/usr/share gem list --local"
    )


def test_gem_packages_command_threads_the_executable():
    """Ansible's ``executable``: a relocated gem is named verbatim (and quoted
    when it carries a metacharacter), so an rbenv/rvm shim is addressable."""
    assert GemPackages.command(gem_bin="/usr/local/bin/gem") == (
        "/usr/local/bin/gem list --local"
    )


def test_gem_packages_command_quotes_a_hostile_override():
    """A path carrying a metacharacter is quoted, so it never reaches the
    remote shell unescaped."""
    hostile = "/opt/gems; rm -rf /"
    assert GemPackages.command(gem_home=hostile) == (
        f"GEM_HOME={shlex.quote(hostile)} gem list --local"
    )


# --- Fact: real-transport parsing (live gem shapes) ---------------------------

# A listing as a live gem prints it: the header, a single-version row, a
# multi-version row, a ``default:`` annotation, a trailing `` default`` and a
# platform-suffixed version.
LISTING_OUTPUT = """\
*** LOCAL GEMS ***

pydash (3.48)
json (2.6.3, 2.6.1)
stringio (default: 3.0.1)
bigdecimal (3.1.4 default)
nokogiri (1.16.5 x86_64-linux)
"""


async def test_gem_packages_parses_the_listing():
    """Over the real-transport path every ``name (versions)`` row contributes
    one entry per version; the header is discarded, versions are sorted, and
    the gathered command is the fact's own."""
    host = CannedShellHost(LISTING_OUTPUT)
    assert await GemPackages.process(host) == [
        _entry("bigdecimal", "3.1.4"),
        _entry("json", "2.6.1"),
        _entry("json", "2.6.3"),
        _entry("nokogiri", "1.16.5"),
        _entry("pydash", "3.48"),
        _entry("stringio", "3.0.1"),
    ]
    assert host.last_shell == GemPackages.command()


async def test_gem_packages_parses_pyinfras_fixture():
    """pyinfra's own fact fixture: ``pydash (3.48)`` reads as the single
    ``{"pydash": ["3.48"]}`` entry -- reemote reports it as one
    ``GemPackageInfo``."""
    assert await GemPackages.process(CannedShellHost("pydash (3.48)\n")) == [
        _entry("pydash", "3.48")
    ]


async def test_gem_packages_keeps_rows_pyinfras_regex_drops():
    """Deliberate widening of pyinfra's ``^([a-zA-Z0-9\\-\\+\\_]+)\\s\\(([0-9\\.]+)\\)$``:
    a name carrying a ``.``, a multi-version row and a prerelease version all
    survive here, where pyinfra silently discards them."""
    output = "io-console (0.6.0)\nrails (7.1.0, 7.0.0)\ncandidate (1.1.0.beta4)\n"
    assert await GemPackages.process(CannedShellHost(output)) == [
        _entry("candidate", "1.1.0.beta4"),
        _entry("io-console", "0.6.0"),
        _entry("rails", "7.0.0"),
        _entry("rails", "7.1.0"),
    ]


async def test_gem_packages_unparsable_output_reads_empty():
    """The bare-store answer (only a header) and output with no listing rows at
    all read as ``[]``, the counterpart of pyinfra's ``default = dict``."""
    assert await GemPackages.process(CannedShellHost("*** LOCAL GEMS ***\n")) == []
    assert await GemPackages.process(CannedShellHost("")) == []
    assert await GemPackages.process(CannedShellHost("gem: not found")) == []


# --- Fact: SimulatedHost seam -------------------------------------------------


async def test_gem_packages_reads_the_simulated_seam():
    """A SimulatedHost answers from its ``gem_packages`` store, bypassing the
    shell; the default empty store reads as ``[]``."""
    host = SimulatedHost(name="h", gem_packages={"system": {"rails": ["7.1.0"]}})
    assert await GemPackages.process(host) == [_entry("rails", "7.1.0")]
    assert await GemPackages.process(SimulatedHost(name="h")) == []


async def test_gem_packages_seam_reports_every_version():
    """gem keeps several versions installed at once, so the seam projects one
    entry per name/version pair, sorted and de-duplicated."""
    host = SimulatedHost(
        name="h", gem_packages={"system": {"rails": ["7.1.0", "7.0.0", "7.1.0"]}}
    )
    assert await GemPackages.process(host) == [
        _entry("rails", "7.0.0"),
        _entry("rails", "7.1.0"),
    ]


async def test_gem_packages_seam_addresses_one_scope():
    """The scope a read names selects the tree it answers from, so a system, a
    user and a relocated store on one host never cross-report."""
    host = SimulatedHost(
        name="h",
        gem_packages={
            "system": {"rails": ["7.1.0"]},
            "user-install": {"paint": ["2.3.0"]},
            "gem_home=/opt/gems": {"rspec": ["3.12.0"]},
        },
    )
    assert await GemPackages.process(host) == [_entry("rails", "7.1.0")]
    assert await GemPackages.process(host, user_install=True) == [
        _entry("paint", "2.3.0")
    ]
    assert await GemPackages.process(host, gem_home="/opt/gems") == [
        _entry("rspec", "3.12.0")
    ]
    # A scope the simulation holds no tree for is nothing installed there.
    assert await GemPackages.process(host, gem_home="/srv/other") == []


async def test_gem_packages_seam_returns_copies():
    """Mutating a returned entry never touches the simulated state, so the
    seam hands back freshly built dicts."""
    host = SimulatedHost(
        name="h", gem_packages={"system": {"a": ["1"], "b": ["2"]}}
    )
    entries = await GemPackages.process(host)
    entries[0]["version"] = "tampered"
    assert await GemPackages.process(host) == [_entry("a", "1"), _entry("b", "2")]


async def test_gem_packages_threads_scope_over_the_real_path():
    """The operation's scope arguments reach the fact's command, so the listing
    always reports the tree the commands mutate."""
    host = CannedShellHost("")
    await GemPackages.process(host, user_install=True)
    assert host.last_shell == GemPackages.command(user_install=True)
    await GemPackages.process(host, gem_home="/opt/gems")
    assert host.last_shell == GemPackages.command(gem_home="/opt/gems")


async def test_gather_fact_gem_packages_fans_out():
    """Across a simulated fleet each host answers from its own seam store,
    keyed by host name."""
    host1 = SimulatedHost(name="web1", gem_packages={"system": {"rails": ["7.1.0"]}})
    host2 = SimulatedHost(name="web2")
    answers = await gather_fact([host1, host2], GemPackages)
    assert answers == {"web1": [_entry("rails", "7.1.0")], "web2": []}


# --- Operation: command descriptors -------------------------------------------


def test_gem_install_command_renders_the_system_scope():
    """The scope flag is rendered in *both* directions, so the tree written is
    always the one the fact read -- never gem's own writable-GEM_HOME
    heuristic."""
    assert GemInstallCommand(packages=("rails:7.1.0", "rspec")).shell() == (
        "gem install --no-user-install rails:7.1.0 rspec"
    )


def test_gem_install_command_renders_the_user_scope():
    assert GemInstallCommand(packages=("paint",), user_install=True).shell() == (
        "gem install --user-install paint"
    )


def test_gem_install_command_threads_bindir_and_env():
    """Ansible's ``bindir`` and the ``GEM_HOME``/``GEM_PATH`` overrides render
    immediately before ``gem`` (the assignments) and after the verb (bindir)."""
    cmd = GemInstallCommand(
        packages=("rspec",), bindir="/usr/local/bin", gem_home="/opt/gems"
    )
    assert cmd.shell() == (
        "GEM_HOME=/opt/gems gem install --no-user-install "
        "--bindir /usr/local/bin rspec"
    )


def test_gem_install_command_quotes_a_hostile_token():
    """A metacharacter inside a token stays quoted, so it never reaches the
    remote shell unescaped."""
    hostile = "rails;touch /tmp/pwned"
    assert GemInstallCommand(packages=(hostile,)).shell() == (
        f"gem install --no-user-install {shlex.quote(hostile)}"
    )


def test_gem_update_command_renders_the_update_verb():
    """pyinfra's ``upgrade_command``: ``gem update``, bare names only."""
    assert GemUpdateCommand(packages=("rails",)).shell() == (
        "gem update --no-user-install rails"
    )


def test_gem_remove_command_renders_non_interactive_flags():
    """A deliberate deviation from pyinfra's bare ``gem uninstall <pkg>``,
    which stops at prompts an unattended deploy cannot answer: an unpinned
    removal carries ``-a`` (all versions), a pinned one ``--version``, and both
    carry ``-x`` (drop executables) and ``-I`` (ignore dependents)."""
    assert GemRemoveCommand(names=("bower",)).shell() == (
        "gem uninstall --no-user-install -a -x -I bower"
    )
    assert GemRemoveCommand(names=("rails",), version="7.1.0").shell() == (
        "gem uninstall --no-user-install --version 7.1.0 -x -I rails"
    )
    assert GemRemoveCommand(names=("paint",), user_install=True).shell() == (
        "gem uninstall --user-install -a -x -I paint"
    )


def test_gem_commands_omit_empty_lists():
    """A command with no tokens stays bare, so no trailing space reaches the
    log."""
    assert GemInstallCommand(packages=()).shell() == "gem install --no-user-install"
    assert GemUpdateCommand(packages=()).shell() == "gem update --no-user-install"


def test_gem_commands_are_elevated_by_sudo():
    """Installing into the root-owned system store needs root, so the request's
    ``sudo=True`` prefixes the rendered shell (the run-as user comes from the
    inventory ``privilege``, never the operation)."""
    cmd = GemInstallCommand(packages=("rails",))
    assert command_shell(cmd) == "gem install --no-user-install rails"
    assert command_shell(cmd, sudo=True) == (
        "sudo gem install --no-user-install rails"
    )
    remove = GemRemoveCommand(names=("rails",))
    assert command_shell(remove, su=True) == f"su -c {shlex.quote(remove.shell())}"



# --- Operation: idempotence ---------------------------------------------------


async def test_gem_packages_installs_missing_packages():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gem.packages(ctx, packages=("rspec", "rails"))]
    assert ctx.changed
    assert ctx.reason == "2 package(s) to install"
    assert commands == [GemInstallCommand(packages=("rspec", "rails"))]


async def test_gem_packages_no_change_when_present():
    host = SimulatedHost(name="h", gem_packages={"system": {"rails": ["7.1.0"]}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gem.packages(ctx, packages=("rails",))]
    assert not ctx.changed
    assert ctx.reason == "all packages present"  # recorded even when unchanged
    assert commands == []


async def test_gem_packages_accepts_a_bare_string():
    """pyinfra accepts ``packages="rails"`` as shorthand for a one-item list."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gem.packages(ctx, packages="rails")]
    assert commands == [GemInstallCommand(packages=("rails",))]


async def test_gem_packages_none_is_a_noop():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gem.packages(ctx)]
    assert not ctx.changed
    assert ctx.reason == "no packages requested"
    assert commands == []


async def test_gem_packages_installs_only_the_missing_pin():
    """pyinfra's ``add_packages`` shape: rails is installed at 7.0.0, so the
    ``rails=7.1.0`` pin is a delta and is fed to gem as ``rails:7.1.0``."""
    host = SimulatedHost(name="h", gem_packages={"system": {"rails": ["7.0.0"]}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in gem.packages(ctx, packages=("rails=7.1.0", "rspec"))
    ]
    assert ctx.reason == "2 package(s) to install"
    assert commands == [GemInstallCommand(packages=("rails:7.1.0", "rspec"))]


async def test_gem_packages_pin_present_only_on_exact_version():
    """A pinned ``<pkg>=<version>`` is present only when that *exact* version
    is installed (``ensure_packages``' rule, apt's/apk's/npm's)."""
    host = SimulatedHost(name="h", gem_packages={"system": {"rails": ["7.1.0"]}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gem.packages(ctx, packages=("rails=7.1.0",))]
    assert not ctx.changed
    assert ctx.reason == "all packages present"
    assert commands == []


async def test_gem_packages_present_when_any_installed_version_matches():
    """gem keeps several versions side by side, so a pin is satisfied when the
    requested version is *one of* the installed ones -- not only the newest."""
    host = SimulatedHost(
        name="h", gem_packages={"system": {"rails": ["7.0.0", "7.1.0"]}}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in gem.packages(ctx, packages=("rails=7.0.0",))]
    assert not ctx.changed
    assert commands == []


# --- Operation: latest --------------------------------------------------------


async def test_gem_packages_latest_upgrades_installed_unpinned_packages():
    """``latest`` updates present, unpinned gems with ``gem update`` --
    pyinfra's ``upgrade_command`` -- and missing ones still install, the
    install command first."""
    host = SimulatedHost(name="h", gem_packages={"system": {"rails": ["7.0.0"]}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in gem.packages(ctx, packages=("rails", "rspec"), latest=True)
    ]
    assert commands == [
        GemInstallCommand(packages=("rspec",)),
        GemUpdateCommand(packages=("rails",)),
    ]
    assert commands[1].shell() == "gem update --no-user-install rails"
    assert ctx.reason == "1 package(s) to install; 1 package(s) to upgrade"


async def test_gem_packages_latest_skips_version_pins():
    """A pinned ``<pkg>=<version>`` is never an upgrade candidate."""
    host = SimulatedHost(name="h", gem_packages={"system": {"rails": ["7.1.0"]}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in gem.packages(ctx, packages=("rails=7.1.0",), latest=True)
    ]
    assert not ctx.changed
    assert ctx.reason == "all packages present"
    assert commands == []


# --- Operation: removal -------------------------------------------------------


async def test_gem_packages_removes_an_installed_bare_name():
    """A bare name removes *all* versions (the ``-a`` flag)."""
    host = SimulatedHost(
        name="h", gem_packages={"system": {"rails": ["7.0.0", "7.1.0"]}}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in gem.packages(ctx, packages=("rails",), present=False)
    ]
    assert ctx.changed
    assert ctx.reason == "1 package(s) to remove"
    assert commands == [GemRemoveCommand(names=("rails",))]
    assert commands[0].shell() == "gem uninstall --no-user-install -a -x -I rails"


async def test_gem_packages_removes_a_matched_pin_as_its_own_command():
    """A pinned removal is its own command with ``--version``, since
    ``gem uninstall`` has no ``name:version`` token."""
    host = SimulatedHost(name="h", gem_packages={"system": {"rails": ["7.1.0"]}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in gem.packages(ctx, packages=("rails=7.1.0",), present=False)
    ]
    assert ctx.changed
    assert ctx.reason == "1 package(s) to remove"
    assert commands == [GemRemoveCommand(names=("rails",), version="7.1.0")]


async def test_gem_packages_removal_skips_a_mismatched_pin():
    """pyinfra's version-aware removal: ``rails=7.1.0`` is *not* removed while
    only 7.0.0 is installed, because the pin is what the request asked to be
    absent."""
    host = SimulatedHost(name="h", gem_packages={"system": {"rails": ["7.0.0"]}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in gem.packages(ctx, packages=("rails=7.1.0",), present=False)
    ]
    assert not ctx.changed
    assert ctx.reason == "no packages to remove"
    assert commands == []


async def test_gem_packages_removal_of_an_absent_package_is_a_noop():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in gem.packages(ctx, packages=("rails",), present=False)
    ]
    assert not ctx.changed
    assert ctx.reason == "no packages to remove"
    assert commands == []


async def test_gem_packages_bare_removal_subsumes_a_pin_on_the_same_name():
    """A bare name removes all versions with ``-a``, so a pin on the same name
    rides that removal rather than becoming a second ``--version`` command."""
    host = SimulatedHost(name="h", gem_packages={"system": {"rails": ["7.1.0"]}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in gem.packages(
            ctx, packages=("rails", "rails=7.1.0"), present=False
        )
    ]
    assert commands == [GemRemoveCommand(names=("rails",))]
    assert ctx.reason == "1 package(s) to remove"


async def test_gem_packages_removal_batches_bare_names():
    """Several bare names share one ``-a`` removal command, in request order."""
    host = SimulatedHost(
        name="h", gem_packages={"system": {"rails": ["7.1.0"], "rspec": ["3.12.0"]}}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in gem.packages(
            ctx, packages=("rails", "rspec"), present=False
        )
    ]
    assert commands == [GemRemoveCommand(names=("rails", "rspec"))]
    assert ctx.reason == "2 package(s) to remove"


# --- Operation: scope ---------------------------------------------------------


async def test_gem_packages_user_install_and_overrides_are_exclusive():
    """``user_install`` and an explicit ``gem_home``/``gem_path`` are two ways
    of choosing the store, so the combination is refused up front -- both are
    static request data, so raising here cannot make the two phases disagree."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="one scope choice, not both"):
        _ = [
            c
            async for c in gem.packages(
                ctx, packages=("rails",), user_install=True, gem_home="/opt/gems"
            )
        ]
    with pytest.raises(ValueError, match="one scope choice, not both"):
        _ = [
            c
            async for c in gem.packages(
                ctx, packages=("rails",), user_install=True, gem_path="/opt/gems"
            )
        ]


async def test_gem_packages_user_scope_reads_and_writes_one_tree():
    """``user_install`` keys the fact read *and* every command with the user
    scope, so the user tree -- not the system one -- converges."""
    host = SimulatedHost(
        name="h", gem_packages={"user-install": {"paint": ["2.3.0"]}}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in gem.packages(
            ctx, packages=("paint", "rspec"), user_install=True
        )
    ]
    assert ctx.changed
    assert commands == [GemInstallCommand(packages=("rspec",), user_install=True)]
    assert commands[0].shell() == "gem install --user-install rspec"


async def test_gem_packages_gem_home_keys_the_relocated_store():
    """The relocated ``GEM_HOME`` the request names is the tree the fact reads,
    so a gem installed in the system store is not mistaken for one under
    ``/opt/gems``."""
    host = SimulatedHost(
        name="h",
        gem_packages={"system": {"rails": ["7.1.0"]}, "gem_home=/opt/gems": {}},
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in gem.packages(ctx, packages=("rails",), gem_home="/opt/gems")
    ]
    assert ctx.reason == "1 package(s) to install"
    assert commands[0].shell() == (
        "GEM_HOME=/opt/gems gem install --no-user-install rails"
    )


# --- Operation: kwargs absorption ---------------------------------------------


async def test_gem_packages_absorbs_the_pyinfra_globals():
    """``**kwargs`` takes pyinfra's global arguments and any unknown keyword
    with no effect, like every sibling module."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in gem.packages(
            ctx, packages=("rails",), untar=False, clean=False, quote=True
        )
    ]
    assert commands == [GemInstallCommand(packages=("rails",))]


# --- Operation: SimulatedHost seam dispatch ------------------------------------


async def test_gem_install_seam_records_the_addressed_scope():
    """``send_command`` maps ``GemInstallCommand`` onto the store its scope
    names: a pin appends that version (gem keeps versions side by side) and an
    unpinned new name gets the ``[""]`` placeholder."""
    host = SimulatedHost(name="h", gem_packages={"system": {"rails": ["7.0.0"]}})
    await send_command(
        host, GemInstallCommand(packages=("rails:7.1.0", "rspec", "paint"))
    )
    assert host.gem_packages == {
        "system": {"rails": ["7.0.0", "7.1.0"], "rspec": [""], "paint": [""]}
    }
    # A user-scope install never lands in the system store.
    await send_command(host, GemInstallCommand(packages=("nokogiri",), user_install=True))
    assert host.gem_packages["user-install"] == {"nokogiri": [""]}


async def test_gem_update_seam_leaves_versions():
    """The ``latest`` path has no registry to resolve newer versions against,
    so the simulated update leaves state untouched."""
    host = SimulatedHost(name="h", gem_packages={"system": {"rails": ["7.0.0"]}})
    await send_command(host, GemUpdateCommand(packages=("rails",)))
    assert host.gem_packages == {"system": {"rails": ["7.0.0"]}}


async def test_gem_remove_seam_drops_the_name_or_version():
    """A bare removal pops the whole name; a pinned one drops only that
    version, popping the name once none remain; an absent name or scope is
    tolerated."""
    host = SimulatedHost(
        name="h",
        gem_packages={"system": {"rails": ["7.0.0", "7.1.0"], "rspec": ["3.12.0"]}},
    )
    await send_command(host, GemRemoveCommand(names=("rails",), version="7.1.0"))
    assert host.gem_packages["system"]["rails"] == ["7.0.0"]
    await send_command(host, GemRemoveCommand(names=("rails",), version="7.0.0"))
    assert "rails" not in host.gem_packages["system"]
    await send_command(host, GemRemoveCommand(names=("rspec",)))
    assert host.gem_packages == {"system": {}}
    await send_command(host, GemRemoveCommand(names=("absent",), user_install=True))


# --- Operation: end to end ----------------------------------------------------

# One inventory entry, so ``run`` materializes one simulated host through the
# injected factory and hands it back for the state assertions.
INVENTORY = [
    {"host": "h", "family": "Ubuntu", "connection": {"address": "127.0.0.1", "username": "u"}}
]


def install_deploy(host):
    yield op(
        gem.packages,
        name="Install gems",
        packages=("rails=7.1.0", "rspec"),
        sudo=True,
    )


async def test_gem_packages_end_to_end_install_then_noop():
    """Issue #57's acceptance criteria through ``run``: the dry run plans the
    pinned system install under sudo and sends nothing, the applied run
    converges the host, and the second pass reports no change."""
    host = SimulatedHost(name="h")

    def factory(entry: dict) -> SimulatedHost:
        return host

    plans, dry_report, _hosts = await run(
        INVENTORY, install_deploy, host_factory=factory, dry=True
    )
    assert dry_report is None
    assert plans["h"][0].changed
    assert plans["h"][0].reason == "2 package(s) to install"
    assert host.gem_packages == {}  # a dry run sends nothing

    _plans, report, _hosts = await run(INVENTORY, install_deploy, host_factory=factory)
    log = format_log(report)
    assert "$ sudo gem install --no-user-install rails:7.1.0 rspec" in log
    assert host.gem_packages == {"system": {"rails": ["7.1.0"], "rspec": [""]}}

    plans2, report2, _hosts = await run(INVENTORY, install_deploy, host_factory=factory)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "all packages present"
    log2 = format_log(report2)
    assert "[no change: all packages present]" in log2
    assert "$ " not in log2


async def test_gem_packages_end_to_end_removal_cycle():
    """One deploy installs then removes: the apply log shows both shells and
    the removal -- a fresh step with its own fact cache -- sees the install's
    mutation."""

    def cycle_deploy(host):
        yield op(gem.packages, name="Install rails", packages=("rails",))
        yield op(
            gem.packages, name="Remove rails", packages=("rails",), present=False
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], cycle_deploy)
    assert plans["h"][0].changed
    # Prepare reads pre-deployment state only: the still-empty store has
    # nothing to remove.
    assert not plans["h"][1].changed
    assert plans["h"][1].reason == "no packages to remove"
    report = await run_apply(runtimes, plans=plans)
    log = format_log(report)
    assert "$ gem install --no-user-install rails" in log
    assert "$ gem uninstall --no-user-install -a -x -I rails" in log
    assert host.gem_packages == {"system": {}}


async def test_gem_packages_dry_run_plans_without_sending():
    """``dry=True`` stops after prepare, so a host whose store already has one
    of two names plans only the other and stays untouched."""

    def deploy(host):
        yield op(gem.packages, name="Ensure", packages=("rails", "rspec"))

    plans, report, hosts = await run(
        INVENTORY,
        deploy,
        host_factory=lambda entry: SimulatedHost(
            name=entry["host"], gem_packages={"system": {"rails": ["7.1.0"]}}
        ),
        dry=True,
    )
    assert report is None
    assert plans["h"][0].changed
    assert plans["h"][0].reason == "1 package(s) to install"
    assert hosts[0].gem_packages == {"system": {"rails": ["7.1.0"]}}


# --- Exports ------------------------------------------------------------------


def test_gem_module_exports():
    """The package re-exports the fact, its entry type, the operation and the
    descriptors, so both ``gem.packages`` and ``gem.GemPackages`` resolve."""
    assert gem.GemPackages is GemPackages
    assert gem.GemPackageInfo is GemPackageInfo
    assert gem.packages is not None
    assert gem.GemInstallCommand is GemInstallCommand
    assert gem.GemUpdateCommand is GemUpdateCommand
    assert gem.GemRemoveCommand is GemRemoveCommand
