# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the npm module: the ``NpmPackages`` fact and ``npm.packages``.

Covers ``reemote.modules.npm.npm.NpmPackages`` -- the installed npm packages
as ``NpmPackageInfo`` entries -- both over the real-transport path (a
``CannedShellHost`` returning canned ``npm ls --json`` output, including the
shapes a live npm 11 emits: an ``npm warn`` line ahead of the document, the
top-level ``problems``/``error`` keys, and a declared-but-uninstalled
dependency flagged ``missing`` with no version) and through the
``SimulatedHost`` seam, plus the ``directory``/``prefix`` arguments threading
the npm scope and the ``gather_fact`` fan-out.

The ``npm.packages`` operation -- a pyinfra port reading ``NpmPackages``,
extended to the surface the Ansible ``npm`` module needs (issue #40) -- is
exercised over its four command descriptors (``NpmInstallCommand`` /
``NpmUpdateCommand`` / ``NpmRemoveCommand`` / ``NpmPackageJsonCommand``),
their rendering (pyinfra's own command builders verbatim: ``npm install -g``,
``npm update -g``, ``npm uninstall -g`` and the ``cd <directory> && npm
install`` project form, with the quoting fixture's hostile directory),
elevation and shell quoting, the version-aware idempotence (exact ``=`` pins
translated to npm's ``@``, scoped names, removal of matched pins only), the
``latest`` upgrade path, the stateless ``package_json`` direction yielded
before any package command, the ``global_``/``directory`` scope validation,
the ``prefix``/``unsafe_perm`` command environment, the ``SimulatedHost`` seam
dispatch (per-scope stores and the manifest read from the shared
``remote_files`` store), and end to end through ``run_prepare`` +
``run_apply`` (sudo elevation, per-step fresh-cache no-ops, and a dry run).

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
from reemote.modules import npm
from reemote.modules.npm.npm import (
    NpmInstallCommand,
    NpmPackageInfo,
    NpmPackageJsonCommand,
    NpmPackages,
    NpmRemoveCommand,
    NpmUpdateCommand,
    _environment_key,
    _npm_env,
    _npm_package_spec,
    _npm_split_spec,
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


def _entry(name: str, version: str) -> NpmPackageInfo:
    """One installed ``NpmPackageInfo`` entry, spelled once per test."""
    return {"name": name, "version": version}


# --- Shared helpers -----------------------------------------------------------


def test_split_version_splits_at_the_last_equals():
    """reemote's one user-facing pin spelling, shared with apt/dnf/apk: the
    split is at the *last* ``=``, so a version carrying one stays whole, and
    a scoped name is not cut at its leading ``@``."""
    assert _split_version("node-sass") == ("node-sass", None)
    assert _split_version("node-sass=9.0.0") == ("node-sass", "9.0.0")
    assert _split_version("@qwen-code/qwen-code=0.8.2") == (
        "@qwen-code/qwen-code",
        "0.8.2",
    )
    assert _split_version("odd=1=2") == ("odd=1", "2")


def test_npm_package_spec_translates_the_pin_to_npm_grammar():
    """The request's ``=`` becomes npm's ``@``; an unpinned token -- a bare
    name or a scoped name whose only ``@`` leads it -- passes through."""
    assert _npm_package_spec("jslint=0.9.3") == "jslint@0.9.3"
    assert _npm_package_spec("@scope/name=1.2.3") == "@scope/name@1.2.3"
    assert _npm_package_spec("node-sass") == "node-sass"
    assert _npm_package_spec("@scope/name") == "@scope/name"


def test_npm_split_spec_inverts_the_translation():
    """The seam's reader splits at the last ``@``, and only when one follows
    the token's first character, so a scoped name keeps its leading ``@``."""
    assert _npm_split_spec("jslint@0.9.3") == ("jslint", "0.9.3")
    assert _npm_split_spec("node-sass") == ("node-sass", None)
    assert _npm_split_spec("@scope/name") == ("@scope/name", None)
    assert _npm_split_spec("@scope/name@1.2.3") == ("@scope/name", "1.2.3")


def test_npm_env_renders_the_command_environment():
    """Ansible's task ``environment:`` block, reduced to the two variables
    npm itself reads; the prefix is quoted and nothing precedes ``npm`` when
    neither is asked for."""
    assert _npm_env(None, False) == ""
    assert _npm_env("/usr/local/lib/npm", False) == (
        "NPM_CONFIG_PREFIX=/usr/local/lib/npm "
    )
    assert _npm_env(None, True) == "NPM_CONFIG_UNSAFE_PERM=true "
    hostile = "/usr/local/lib/npm; rm -rf /"
    assert _npm_env(hostile, True) == (
        f"NPM_CONFIG_PREFIX={shlex.quote(hostile)} NPM_CONFIG_UNSAFE_PERM=true "
    )


def test_environment_key_names_the_npm_scope():
    """One key per tree npm keeps -- the default global prefix, a relocated
    one, and each project directory -- so the fact seam and the mutators
    address the same store."""
    assert _environment_key(None, None) == "global"
    assert _environment_key(None, "/usr/local/lib/npm") == (
        "global@/usr/local/lib/npm"
    )
    assert _environment_key("/srv/app", None) == "directory=/srv/app"
    assert _environment_key("/srv/app", "/ignored") == "directory=/srv/app"


# --- Fact: command parity -----------------------------------------------------


def test_npm_packages_command_is_the_global_json_listing():
    """``npm ls -g --depth=0 --json``, the probe the issue's workaround
    hand-rolled: depth 0 because that is the tree ``npm install`` mutates."""
    assert NpmPackages.command() == "npm ls -g --depth=0 --json"


def test_npm_packages_command_reports_the_project_tree():
    """Deliberate deviation from pyinfra, whose directory variant still ran
    ``npm list -g`` and so reported the global tree the local install never
    touches. The ``! test -d`` guard keeps a missing directory an empty
    answer rather than an error."""
    assert NpmPackages.command(directory="/srv/app") == (
        "! test -d /srv/app || (cd /srv/app && npm ls --depth=0 --json)"
    )


def test_npm_packages_command_threads_the_prefix():
    """``NPM_CONFIG_PREFIX`` relocates the global tree the fact reads, so the
    fact reports the environment ``npm install -g`` will mutate."""
    assert NpmPackages.command(prefix="/usr/local/lib/npm") == (
        "NPM_CONFIG_PREFIX=/usr/local/lib/npm npm ls -g --depth=0 --json"
    )
    # The prefix rides inside the guard too, right before npm.
    assert NpmPackages.command(directory="/srv/app", prefix="/p") == (
        "! test -d /srv/app || (cd /srv/app "
        "&& NPM_CONFIG_PREFIX=/p npm ls --depth=0 --json)"
    )


def test_npm_packages_command_shell_quotes_the_directory():
    """pyinfra's hostile-directory fixture: a path with a metacharacter is
    quoted, so it never reaches the remote shell unescaped."""
    hostile = "/app dir; rm -rf /"
    assert NpmPackages.command(directory=hostile) == (
        f"! test -d {shlex.quote(hostile)} "
        f"|| (cd {shlex.quote(hostile)} && npm ls --depth=0 --json)"
    )


# --- Fact: real-transport parsing (live npm 11 shapes) ------------------------

# A global listing as a live npm prints it: an ``npm warn`` line ahead of the
# document, scoped and unscoped names, and the ``overridden`` key beside each
# version.
GLOBAL_OUTPUT = """\
npm warn Unknown builtin config "globalignorefile". This will stop working in the next major version of npm.
{
  "name": "lib",
  "dependencies": {
    "@qwen-code/qwen-code": {
      "version": "0.8.2",
      "overridden": false
    },
    "node-sass": {
      "version": "9.0.0"
    },
    "npm": {
      "version": "11.8.0",
      "overridden": false
    }
  }
}
"""

GLOBAL_FACT = [
    _entry("@qwen-code/qwen-code", "0.8.2"),
    _entry("node-sass", "9.0.0"),
    _entry("npm", "11.8.0"),
]


async def test_npm_packages_parses_the_json_tree():
    """Over the real-transport path every top-level dependency becomes one
    sorted entry; the leading warning line is discarded, and the gathered
    command is the fact's own."""
    host = CannedShellHost(GLOBAL_OUTPUT)
    assert await NpmPackages.process(host) == GLOBAL_FACT
    assert host.last_shell == NpmPackages.command()


async def test_npm_packages_skips_missing_and_malformed_entries():
    """A declared dependency npm could not install is flagged ``missing``
    and carries only ``required`` -- not installed, so not reported -- and an
    entry that is not a mapping, or has no string version, contributes
    nothing. The top-level ``problems``/``error`` keys of npm's
    exit-1-with-unmet-deps answer are ignored."""
    output = """\
{
  "name": "app",
  "problems": [ "ERESOLVE" ],
  "error": { "code": "ELSPROBLEMS" },
  "dependencies": {
    "left-pad": {
      "required": "^1.3.0",
      "missing": true
    },
    "got": {
      "version": "11.8.6"
    },
    "broken": "not-a-mapping",
    "versionless": {
      "overridden": false
    }
  }
}
"""
    assert await NpmPackages.process(CannedShellHost(output)) == [
        _entry("got", "11.8.6")
    ]


async def test_npm_packages_parses_non_pinned_versions():
    """The JSON reader keeps what pyinfra's ``[0-9.]+`` version class
    rejects: prerelease and build metadata, the ``latest`` dist-tag a global
    install can record, and a dotted date."""
    output = """\
{
  "dependencies": {
    "candidate": { "version": "1.1.0-beta.4" },
    "local": { "version": "2.0.1+build.7" },
    "tagged": { "version": "latest" },
    "dated": { "version": "2024.03.01" }
  }
}
"""
    assert await NpmPackages.process(CannedShellHost(output)) == [
        _entry("candidate", "1.1.0-beta.4"),
        _entry("dated", "2024.03.01"),
        _entry("local", "2.0.1+build.7"),
        _entry("tagged", "latest"),
    ]


async def test_npm_packages_reads_only_the_top_level_tree():
    """``--depth=0`` is the fact's contract: a nested dependency of an
    installed package is npm's own business and is not reported, so a
    transitive name never makes a request look already satisfied."""
    output = """\
{
  "dependencies": {
    "react": {
      "version": "18.2.0",
      "requires": { "loose-envify": "^1.1.0" },
      "dependencies": {
        "loose-envify": { "version": "1.4.0" }
      }
    }
  }
}
"""
    assert await NpmPackages.process(CannedShellHost(output)) == [
        _entry("react", "18.2.0")
    ]


async def test_npm_packages_unparsable_output_reads_empty():
    """``{}`` -- a directory without a manifest, npm's answer for the empty
    project tree -- a document with no dependency map, and output with no
    JSON at all all read as ``[]``, pyinfra's ``default = dict`` projected."""
    assert await NpmPackages.process(CannedShellHost("{}")) == []
    assert await NpmPackages.process(CannedShellHost('{"name": "lib"}')) == []
    assert await NpmPackages.process(CannedShellHost("")) == []
    assert await NpmPackages.process(CannedShellHost("npm: not found")) == []


# --- Fact: SimulatedHost seam -------------------------------------------------


async def test_npm_packages_reads_the_simulated_seam():
    """A SimulatedHost answers from its ``npm_packages`` store, bypassing the
    shell; the default empty store reads as ``[]``, like the baseline's
    ``default = dict`` for a bare environment."""
    host = SimulatedHost(name="h", npm_packages={"global": {"npm": "11.8.0"}})
    assert await NpmPackages.process(host) == [_entry("npm", "11.8.0")]
    assert await NpmPackages.process(SimulatedHost(name="h")) == []


async def test_npm_packages_seam_addresses_one_scope():
    """The scope a read names selects the tree it answers from, so a global
    and a project store on one host never cross-report."""
    host = SimulatedHost(
        name="h",
        npm_packages={
            "global": {"npm": "11.8.0"},
            "global@/opt/npm": {"jslint": "0.9.3"},
            "directory=/srv/app": {"react": "18.2.0"},
        },
    )
    assert await NpmPackages.process(host) == [_entry("npm", "11.8.0")]
    assert await NpmPackages.process(host, prefix="/opt/npm") == [
        _entry("jslint", "0.9.3")
    ]
    assert await NpmPackages.process(host, directory="/srv/app") == [
        _entry("react", "18.2.0")
    ]
    # A scope the simulation holds no tree for is nothing installed there.
    assert await NpmPackages.process(host, directory="/srv/other") == []


async def test_npm_packages_seam_returns_copies():
    """Mutating a returned entry never touches the simulated state, so the
    seam hands back freshly built dicts in sorted order."""
    host = SimulatedHost(name="h", npm_packages={"global": {"a": "1", "b": "2"}})
    entries = await NpmPackages.process(host)
    entries[0]["version"] = "tampered"
    assert await NpmPackages.process(host) == [_entry("a", "1"), _entry("b", "2")]


async def test_npm_packages_threads_scope_over_the_real_path():
    """The operation's scope arguments reach the fact's command, so the
    listing always reports the tree the commands mutate."""
    host = CannedShellHost("")
    await NpmPackages.process(host, directory="/srv/app")
    assert host.last_shell == NpmPackages.command(directory="/srv/app")
    await NpmPackages.process(host, prefix="/opt/npm")
    assert host.last_shell == NpmPackages.command(prefix="/opt/npm")


async def test_gather_fact_npm_packages_fans_out():
    """Across a simulated fleet each host answers from its own seam store,
    keyed by host name."""
    host1 = SimulatedHost(name="web1", npm_packages={"global": {"npm": "11.8.0"}})
    host2 = SimulatedHost(name="web2")
    answers = await gather_fact([host1, host2], NpmPackages)
    assert answers == {
        "web1": [_entry("npm", "11.8.0")],
        "web2": [],
    }


# --- Operation: command descriptors --------------------------------------------


def test_npm_install_command_renders_install_global():
    """pyinfra's global install builder verbatim, the pinned token already in
    npm's ``@`` grammar (``add_packages``' fixture)."""
    cmd = NpmInstallCommand(packages=("git@1.1", "react"))
    assert cmd.shell() == "npm install -g git@1.1 react"


def test_npm_install_command_renders_the_project_form():
    """``cd <directory> && npm install`` -- pyinfra's ``StringCommand`` form
    without the stray ``-g`` its builder also omitted."""
    cmd = NpmInstallCommand(packages=("git@1.1", "react"), directory="testdir")
    assert cmd.shell() == "cd testdir && npm install git@1.1 react"


def test_npm_install_command_quotes_a_hostile_directory():
    """pyinfra's ``directory_needs_quotes`` fixture: the dangerous path is
    quoted whole, so the injected command never runs."""
    cmd = NpmInstallCommand(packages=("react",), directory="/app dir; rm -rf /")
    assert cmd.shell() == "cd '/app dir; rm -rf /' && npm install react"


def test_npm_install_command_quotes_the_package_tokens():
    """A metacharacter inside a token -- reachable through a name the registry
    would reject but the simulation never consults -- stays quoted."""
    hostile = "react;touch /tmp/pwned"
    assert NpmInstallCommand(packages=(hostile,)).shell() == (
        f"npm install -g {shlex.quote(hostile)}"
    )


def test_npm_install_command_threads_the_environment():
    """``prefix`` and ``unsafe_perm`` render as Ansible's task-level variables
    immediately before ``npm``, in the order npm reads them."""
    cmd = NpmInstallCommand(
        packages=("react",),
        prefix="/usr/local/lib/npm",
        unsafe_perm=True,
    )
    assert cmd.shell() == (
        "NPM_CONFIG_PREFIX=/usr/local/lib/npm "
        "NPM_CONFIG_UNSAFE_PERM=true npm install -g react"
    )
    # In the project form the assignments must follow the cd, not lead it.
    local = NpmInstallCommand(packages=("react",), directory="app", prefix="/p")
    assert local.shell() == "cd app && NPM_CONFIG_PREFIX=/p npm install react"


def test_npm_update_and_remove_commands_render_pyinfra_builders():
    """``npm update -g`` and ``npm uninstall -g`` -- pyinfra's upgrade and
    uninstall builders, the ``remove_packages`` fixture's tokens verbatim."""
    assert NpmUpdateCommand(packages=("react",)).shell() == "npm update -g react"
    assert NpmRemoveCommand(packages=("react", "test@1.1")).shell() == (
        "npm uninstall -g react test@1.1"
    )
    local = NpmRemoveCommand(packages=("react",), directory="testdir")
    assert local.shell() == "cd testdir && npm uninstall react"


def test_npm_commands_omit_empty_package_lists():
    """A command with no tokens stays bare, so no trailing space reaches the
    log."""
    assert NpmInstallCommand(packages=()).shell() == "npm install -g"
    assert NpmUpdateCommand(packages=(), directory="app").shell() == (
        "cd app && npm update"
    )


def test_npm_package_json_command_installs_in_the_manifest_directory():
    """Ansible's ``npm: path=`` form: npm reads the manifest itself, so the
    command names no packages and carries no ``-g`` -- the project scope is
    the manifest's own directory. A bare relative filename has no parent to
    change into."""
    assert NpmPackageJsonCommand(path="/srv/app/package.json").shell() == (
        "cd /srv/app && npm install"
    )
    assert NpmPackageJsonCommand(path="package.json").shell() == "npm install"
    hostile = "/srv/a ppr; rm/package.json"
    assert NpmPackageJsonCommand(path=hostile).shell() == (
        f"cd {shlex.quote('/srv/a ppr; rm')} && npm install"
    )
    assert NpmPackageJsonCommand(
        path="/srv/app/package.json", prefix="/p", unsafe_perm=True
    ).shell() == (
        "cd /srv/app && NPM_CONFIG_PREFIX=/p NPM_CONFIG_UNSAFE_PERM=true npm install"
    )


def test_npm_package_commands_are_elevated_by_sudo():
    """Installing into a root-owned global prefix needs root, so the
    request's ``sudo=True`` prefixes the rendered shell (the run-as user
    comes from the inventory ``privilege``, never the operation)."""
    cmd = NpmInstallCommand(packages=("react",))
    assert command_shell(cmd) == "npm install -g react"
    assert command_shell(cmd, sudo=True) == "sudo npm install -g react"
    remove = NpmRemoveCommand(packages=("react",))
    assert command_shell(remove, su=True) == f"su -c {shlex.quote(remove.shell())}"


# --- Operation: idempotence ----------------------------------------------------


async def test_npm_packages_installs_missing_packages():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in npm.packages(ctx, packages=("pyinfra", "react"))]
    assert ctx.changed
    assert ctx.reason == "2 package(s) to install"
    assert commands == [NpmInstallCommand(packages=("pyinfra", "react"))]


async def test_npm_packages_no_change_when_present():
    host = SimulatedHost(name="h", npm_packages={"global": {"react": "18.2.0"}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in npm.packages(ctx, packages=("react",))]
    assert not ctx.changed
    assert ctx.reason == "all packages present"  # recorded even when unchanged
    assert commands == []


async def test_npm_packages_accepts_a_bare_string():
    """pyinfra accepts ``packages="react"`` as shorthand for a one-item list."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in npm.packages(ctx, packages="react")]
    assert commands == [NpmInstallCommand(packages=("react",))]


async def test_npm_packages_none_is_a_noop():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in npm.packages(ctx)]
    assert not ctx.changed
    assert ctx.reason == "no packages requested"
    assert commands == []


async def test_npm_packages_installs_only_the_missing_pin():
    """pyinfra's ``add_packages`` fixture: git is installed at 1.0, so the
    ``git=1.1`` pin is a delta and is fed to npm as ``git@1.1``."""
    host = SimulatedHost(name="h", npm_packages={"global": {"git": "1.0"}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in npm.packages(ctx, packages=("git=1.1", "react"))]
    assert ctx.reason == "2 package(s) to install"
    assert commands == [NpmInstallCommand(packages=("git@1.1", "react"))]


# --- Operation: version pins ----------------------------------------------------


async def test_npm_packages_pin_present_only_on_exact_version():
    """A pinned ``<pkg>=<version>`` is present only when that *exact* version
    is installed (``ensure_packages``' rule, apt's and apk's)."""
    host = SimulatedHost(name="h", npm_packages={"global": {"jslint": "0.9.3"}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in npm.packages(ctx, packages=("jslint=0.9.3",))]
    assert not ctx.changed
    assert ctx.reason == "all packages present"
    assert commands == []


async def test_npm_packages_pin_installs_when_version_differs():
    host = SimulatedHost(name="h", npm_packages={"global": {"jslint": "0.9.2"}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in npm.packages(ctx, packages=("jslint=0.9.3",))]
    assert ctx.changed
    assert commands == [NpmInstallCommand(packages=("jslint@0.9.3",))]
    # The token rides through to the shell in npm's own grammar.
    assert commands[0].shell() == "npm install -g jslint@0.9.3"


async def test_npm_packages_pin_splits_at_the_equals_not_the_at():
    """A scoped name carries a leading ``@`` that must not be read as a pin,
    and the ``=`` split is the shared last-``=`` rule apt and apk follow --
    ``odd=1=2`` is the name ``odd=1`` at version ``2``, which npm's own
    ``rsplit`` on a requirement argument reads the same way."""
    host = SimulatedHost(name="h", npm_packages={"global": {"@scope/name": "1.0"}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in npm.packages(ctx, packages=("@scope/name",))]
    assert not ctx.changed
    assert commands == []
    pinned = SimulatedHost(name="p", npm_packages={"global": {"odd=1": "2"}})
    pctx = OpContext(host=pinned, facts=FactCache(pinned, phase="prepare"))
    assert [c async for c in npm.packages(pctx, packages=("odd=1=2",))] == []


async def test_npm_packages_scoped_pin_renders_both_at_signs():
    """``@scope/name=1.2.3`` becomes npm's ``@scope/name@1.2.3`` -- the
    spelling npm resolves a scoped package by."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in npm.packages(ctx, packages=("@qwen-code/qwen-code=0.8.2",))
    ]
    assert commands[0].shell() == "npm install -g @qwen-code/qwen-code@0.8.2"
    # The scoped pin present at that version is converged, so the leading @
    # never reaches the comparison as a version separator.
    present = SimulatedHost(
        name="h", npm_packages={"global": {"@qwen-code/qwen-code": "0.8.2"}}
    )
    pctx = OpContext(host=present, facts=FactCache(present, phase="prepare"))
    assert (
        [
            c
            async for c in npm.packages(
                pctx, packages=("@qwen-code/qwen-code=0.8.2",)
            )
        ]
        == []
    )


async def test_npm_packages_compares_names_verbatim():
    """No normalization: npm names are case-sensitive on the registry, so
    ``React`` and ``react`` are two requests, unlike pip's PEP 503
    folding."""
    host = SimulatedHost(name="h", npm_packages={"global": {"react": "18.2.0"}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in npm.packages(ctx, packages=("React",))]
    assert ctx.changed
    assert commands == [NpmInstallCommand(packages=("React",))]


# --- Operation: latest ----------------------------------------------------------


async def test_npm_packages_latest_upgrades_installed_unpinned_packages():
    """``latest`` updates present, unpinned packages with ``npm update`` --
    pyinfra's ``upgrade_command``, not pip's ``install --upgrade`` -- and
    missing ones still install, the install command first."""
    host = SimulatedHost(name="h", npm_packages={"global": {"react": "18.2.0"}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in npm.packages(ctx, packages=("react", "pyinfra"), latest=True)
    ]
    assert commands == [
        NpmInstallCommand(packages=("pyinfra",)),
        NpmUpdateCommand(packages=("react",)),
    ]
    assert commands[1].shell() == "npm update -g react"
    assert ctx.reason == "1 package(s) to install; 1 package(s) to upgrade"


async def test_npm_packages_latest_skips_version_pins():
    """A pinned ``<pkg>=<version>`` is never an upgrade candidate."""
    host = SimulatedHost(name="h", npm_packages={"global": {"react": "18.2.0"}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in npm.packages(ctx, packages=("react=18.2.0",), latest=True)
    ]
    assert not ctx.changed
    assert ctx.reason == "all packages present"
    assert commands == []


# --- Operation: removal -----------------------------------------------------------


async def test_npm_packages_removes_installed_packages_verbatim():
    """pyinfra's ``remove_packages`` fixture: a pin whose installed version
    matches is uninstalled with its ``@`` token intact, and a name npm would
    drop regardless keeps its bare spelling."""
    host = SimulatedHost(
        name="h",
        npm_packages={"global": {"react": "18.2.0", "test": "1.1", "left-pad": ""}},
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in npm.packages(
            ctx, packages=("react", "test=1.1", "left-pad"), present=False
        )
    ]
    assert ctx.changed
    assert ctx.reason == "3 package(s) to remove"
    assert commands == [
        NpmRemoveCommand(packages=("react", "test@1.1", "left-pad"))
    ]
    assert commands[0].shell() == "npm uninstall -g react test@1.1 left-pad"


async def test_npm_packages_removal_skips_a_mismatched_pin():
    """The fixture's second half: ``git=1.1`` is *not* removed while git 1.0
    is installed, because the pin is what the request asked to be absent."""
    host = SimulatedHost(name="h", npm_packages={"global": {"git": "1.0"}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in npm.packages(ctx, packages=("git=1.1",), present=False)
    ]
    assert not ctx.changed
    assert ctx.reason == "no packages to remove"
    assert commands == []


async def test_npm_packages_removal_of_an_absent_package_is_a_noop():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in npm.packages(ctx, packages=("react",), present=False)
    ]
    assert not ctx.changed
    assert ctx.reason == "no packages to remove"
    assert commands == []


async def test_npm_packages_removal_dedups_a_repeated_token():
    host = SimulatedHost(name="h", npm_packages={"global": {"react": "18.2.0"}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in npm.packages(
            ctx, packages=("react", "react=18.2.0"), present=False
        )
    ]
    assert commands == [NpmRemoveCommand(packages=("react",))]
    assert ctx.reason == "1 package(s) to remove"


# --- Operation: scope --------------------------------------------------------------


async def test_npm_packages_global_and_directory_are_exclusive():
    """Both arguments are static request data, so validating them here cannot
    make the two phases disagree -- the ``ValueError`` lands in prepare,
    before anything is sent."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="directory only when global_=False"):
        _ = [
            c
            async for c in npm.packages(
                ctx, packages=("react",), directory="/srv/app"
            )
        ]
    with pytest.raises(ValueError, match="needs the directory"):
        _ = [c async for c in npm.packages(ctx, packages=("react",), global_=False)]


async def test_npm_packages_project_scope_reads_and_writes_one_tree():
    """``global_=False`` keys the fact read *and* every command with the same
    directory, so the project tree is what converges."""
    host = SimulatedHost(name="h", npm_packages={"directory=/srv/app": {"react": "18.2.0"}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in npm.packages(
            ctx, packages=("react", "pyinfra"), global_=False, directory="/srv/app"
        )
    ]
    assert ctx.changed
    assert commands == [
        NpmInstallCommand(packages=("pyinfra",), directory="/srv/app")
    ]
    assert commands[0].shell() == "cd /srv/app && npm install pyinfra"


async def test_npm_packages_prefix_keys_the_global_store():
    """The relocated prefix the request names is the tree the fact reads, so
    a package installed under the default prefix is not mistaken for one
    installed under ``/opt/npm``."""
    host = SimulatedHost(
        name="h",
        npm_packages={"global": {"react": "18.2.0"}, "global@/opt/npm": {}},
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in npm.packages(
            ctx, packages=("react",), prefix="/opt/npm", unsafe_perm=True
        )
    ]
    assert ctx.reason == "1 package(s) to install"
    assert commands[0].shell() == (
        "NPM_CONFIG_PREFIX=/opt/npm NPM_CONFIG_UNSAFE_PERM=true "
        "npm install -g react"
    )


# --- Operation: package_json ---------------------------------------------------------


async def test_npm_packages_package_json_is_stateless():
    """The manifest direction always yields -- nothing gates it, because
    resolving a manifest's semver ranges is npm's solver, not a fact read --
    and it is yielded before any package command, pip's ``requirements``
    order."""
    host = SimulatedHost(name="h", npm_packages={"global": {"react": "18.2.0"}})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in npm.packages(
            ctx, package_json="/srv/app/package.json", packages=("react", "pyinfra")
        )
    ]
    assert commands == [
        NpmPackageJsonCommand(path="/srv/app/package.json"),
        NpmInstallCommand(packages=("pyinfra",)),
    ]
    assert ctx.reason == (
        "package.json /srv/app/package.json to install; 1 package(s) to install"
    )
    # A converged package list still replans the manifest.
    converged = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    again = [
        c async for c in npm.packages(converged, package_json="/srv/app/package.json")
    ]
    assert converged.changed
    assert again == [NpmPackageJsonCommand(path="/srv/app/package.json")]


async def test_npm_packages_package_json_cannot_be_removed():
    """npm installs a manifest and has no uninstall-from-manifest form, so
    the combination is refused rather than silently dropped."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    with pytest.raises(ValueError, match="no uninstall-from-manifest form"):
        _ = [
            c
            async for c in npm.packages(
                ctx, package_json="/srv/app/package.json", present=False
            )
        ]


# --- Operation: kwargs absorption -----------------------------------------------------


async def test_npm_packages_absorbs_the_pyinfra_globals():
    """``**kwargs`` takes pyinfra's global arguments and any unknown keyword
    with no effect, like every sibling module."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in npm.packages(
            ctx, packages=("react",), untar=False, clean=False, quote=True
        )
    ]
    assert commands == [NpmInstallCommand(packages=("react",))]


# --- Operation: SimulatedHost seam dispatch --------------------------------------------


async def test_npm_install_seam_records_the_addressed_scope():
    """``send_command`` maps ``NpmInstallCommand`` onto the store its scope
    names: a pin records exactly that version -- OVERWRITING an older one,
    npm's re-pin behavior -- and an unpinned new name gets the ``""``
    placeholder while an installed name keeps its version."""
    host = SimulatedHost(name="h", npm_packages={"global": {"react": "18.2.0"}})
    await send_command(
        host,
        NpmInstallCommand(packages=("react@19.0.0", "@scope/name", "pyinfra")),
    )
    assert host.npm_packages == {
        "global": {"react": "19.0.0", "@scope/name": "", "pyinfra": ""}
    }
    await send_command(host, NpmInstallCommand(packages=("pyinfra",)))
    assert host.npm_packages["global"]["pyinfra"] == ""
    # A different prefix is a different tree, touched and left alone.
    await send_command(
        host, NpmInstallCommand(packages=("jslint@0.9.3",), prefix="/opt/npm")
    )
    assert host.npm_packages["global@/opt/npm"] == {"jslint": "0.9.3"}


async def test_npm_update_seam_leaves_versions():
    """The ``latest`` path has no registry to resolve newer versions
    against, so the simulated update leaves state untouched."""
    host = SimulatedHost(name="h", npm_packages={"global": {"react": "18.2.0"}})
    await send_command(host, NpmUpdateCommand(packages=("react",)))
    assert host.npm_packages == {"global": {"react": "18.2.0"}}


async def test_npm_remove_seam_drops_the_name():
    """Removal pops the token's bare name from the addressed scope, the
    version selector included, and an absent name or scope is tolerated."""
    host = SimulatedHost(
        name="h", npm_packages={"global": {"react": "18.2.0", "@scope/name": "1.0"}}
    )
    await send_command(
        host, NpmRemoveCommand(packages=("react@18.2.0", "@scope/name", "absent"))
    )
    assert host.npm_packages == {"global": {}}
    await send_command(host, NpmRemoveCommand(packages=("react",), directory="/srv"))


async def test_npm_package_json_seam_applies_the_manifest():
    """The manifest descriptor reads its text from the shared
    ``remote_files`` store -- so a ``files.put`` earlier in the deploy is
    visible here -- and records each declared name in the manifest's own
    directory scope. An unseeded path, and a file that is not JSON, apply
    nothing."""
    host = SimulatedHost(name="h")
    host.remote_files["/srv/app/package.json"] = (
        b'{"name": "app", "dependencies": {"react": "^18.2.0"},'
        b' "devDependencies": {"eslint": "8.57.0"}}'
    )
    await send_command(host, NpmPackageJsonCommand(path="/srv/app/package.json"))
    assert host.npm_packages == {"directory=/srv/app": {"react": "", "eslint": ""}}
    host.remote_files["/srv/bad/package.json"] = b"not json at all"
    await send_command(host, NpmPackageJsonCommand(path="/srv/bad/package.json"))
    await send_command(host, NpmPackageJsonCommand(path="/srv/gone/package.json"))
    assert host.npm_packages == {"directory=/srv/app": {"react": "", "eslint": ""}}


# --- Operation: end to end ---------------------------------------------------------------

# One inventory entry, so ``run`` materializes one simulated host through the
# injected factory and hands it back for the state assertions.
INVENTORY = [{"host": "h", "family": "Ubuntu", "connection": {"address": "127.0.0.1", "username": "u"}}]


def install_deploy(host):
    yield op(
        npm.packages,
        name="Install globals",
        packages=("jslint=0.9.3", "node-sass"),
        prefix="/usr/local/lib/npm",
        sudo=True,
    )


async def test_npm_packages_end_to_end_install_then_noop():
    """Issue #40's acceptance criteria through ``run``: the dry run plans the
    pinned global install under sudo with the prefix in its environment and
    sends nothing, the applied run converges the host, and the second pass
    reports no change with no commands."""

    # One host across all three runs: ``Host.aclose`` is a no-op for a
    # simulation that never connects, so the factory can hand the same
    # instance back and the second pass really sees the first one's work.
    host = SimulatedHost(name="h")

    def factory(entry: dict) -> SimulatedHost:
        return host

    plans, dry_report, _hosts = await run(
        INVENTORY, install_deploy, host_factory=factory, dry=True
    )
    assert dry_report is None
    assert plans["h"][0].changed
    assert plans["h"][0].reason == "2 package(s) to install"
    assert host.npm_packages == {}  # a dry run sends nothing

    _plans, report, _hosts = await run(INVENTORY, install_deploy, host_factory=factory)
    log = format_log(report)
    assert (
        "$ sudo NPM_CONFIG_PREFIX=/usr/local/lib/npm "
        "npm install -g jslint@0.9.3 node-sass" in log
    )
    assert host.npm_packages == {
        "global@/usr/local/lib/npm": {"jslint": "0.9.3", "node-sass": ""}
    }

    # The second pass against the converged host: no change, no commands.
    plans2, report2, _hosts = await run(INVENTORY, install_deploy, host_factory=factory)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "all packages present"
    log2 = format_log(report2)
    assert "[no change: all packages present]" in log2
    assert "$ " not in log2


async def test_npm_packages_end_to_end_removal_cycle():
    """One deploy installs then removes: the apply log shows both shells and
    the removal -- a fresh step with its own fact cache -- sees the install's
    mutation, then no-ops once the package is gone."""

    def cycle_deploy(host):
        yield op(npm.packages, name="Install react", packages=("react",))
        yield op(
            npm.packages, name="Remove react", packages=("react",), present=False
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], cycle_deploy)
    assert plans["h"][0].changed
    # Prepare reads pre-deployment state only: the still-empty environment
    # has nothing to remove.
    assert not plans["h"][1].changed
    assert plans["h"][1].reason == "no packages to remove"
    report = await run_apply(runtimes, plans=plans)
    log = format_log(report)
    assert "$ npm install -g react" in log
    assert "$ npm uninstall -g react" in log
    assert host.npm_packages == {"global": {}}
    _runtimes, plans2 = await run_prepare([host], cycle_deploy)
    assert not plans2["h"][1].changed


async def test_npm_packages_end_to_end_project_scope_with_manifest():
    """The project-scope pair the nodejs role needs: the stateless
    ``package_json`` install builds the tree, and the following scoped
    request re-materializes against it and finds nothing to install."""

    def deploy(host):
        yield op(
            npm.packages,
            name="Install app dependencies",
            package_json="/srv/app/package.json",
        )
        yield op(
            npm.packages,
            name="Ensure react",
            packages=("react",),
            global_=False,
            directory="/srv/app",
        )

    host = SimulatedHost(name="h")
    host.remote_files["/srv/app/package.json"] = b'{"dependencies": {"react": "^18.2.0"}}'
    runtimes, plans = await run_prepare([host], deploy)
    # Prepare reads the untouched project tree: the scoped request plans an
    # install the apply phase will not need.
    assert plans["h"][1].changed
    report = await run_apply(runtimes, plans=plans)
    log = format_log(report)
    assert "$ cd /srv/app && npm install" in log
    # The scoped request re-materialized against the manifest's tree: nothing
    # left to install, so its shell never appears.
    assert "cd /srv/app && npm install react" not in log
    assert "[no change: all packages present]" in log
    assert host.npm_packages == {"directory=/srv/app": {"react": ""}}


async def test_npm_packages_dry_run_plans_without_sending():
    """``dry=True`` stops after prepare, so a host whose tree already has
    one of two names plans only the other and stays untouched."""

    def deploy(host):
        yield op(npm.packages, name="Ensure", packages=("react", "pyinfra"))

    plans, report, hosts = await run(
        INVENTORY,
        deploy,
        host_factory=lambda entry: SimulatedHost(
            name=entry["host"], npm_packages={"global": {"react": "18.2.0"}}
        ),
        dry=True,
    )
    assert report is None
    assert plans["h"][0].changed
    assert plans["h"][0].reason == "1 package(s) to install"
    assert hosts[0].npm_packages == {"global": {"react": "18.2.0"}}
