# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the pip module: the ``PipPackages`` fact and ``pip.packages``.

Covers ``reemote.modules.pip.pip.PipPackages`` -- the installed pip
packages as ``PipPackageInfo`` entries, PEP 503-normalized names included --
both over the real-transport path (a ``CannedShellHost`` returning canned
``pip freeze --all`` output, with the pyinfra fact's own ``PIP_REGEX`` row
shapes: pinned rows, ``-e`` editables and direct references skipped) and
through the ``SimulatedHost`` seam, plus the ``pip`` executable argument
threading and the ``gather_fact`` fan-out.

The ``pip.packages`` operation -- a pyinfra port reading ``PipPackages`` --
is exercised over its four command descriptors (``PipInstallCommand`` /
``PipUpgradeCommand`` / ``PipRemoveCommand`` / ``PipRequirementsCommand``),
their rendering (pyinfra's own command builders verbatim: ``pip install``,
``pip install ... --upgrade``, ``pip uninstall --yes``, ``... -r <file>``),
elevation and shell quoting, the version-aware idempotence (exact ``==``
pins, PEP 503-normalized comparison, other specifiers degrading to
name-only presence), the ``latest`` upgrade path, the stateless
``requirements`` direction yielded before any package command, the
dropped ``virtualenv``/``virtualenv_kwargs`` absorbed by ``**kwargs``, the
``SimulatedHost`` seam dispatch (including the requirements file read from
the shared ``remote_files`` store), and end-to-end through
``run_prepare`` + ``run_apply`` (sudo elevation, per-step fresh-cache
no-ops, and a dry run).

``asyncio_mode = "auto"`` lets the async tests run without decorators.
"""

import shlex

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
from reemote.modules import pip
from reemote.modules.pip.pip import (
    PipInstallCommand,
    PipPackageInfo,
    PipPackages,
    PipRemoveCommand,
    PipRequirementsCommand,
    PipUpgradeCommand,
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


def _entry(name: str, version: str) -> PipPackageInfo:
    """One installed ``PipPackageInfo`` entry, spelled once per test."""
    return {"name": name, "version": version}


# --- Fact: command parity -----------------------------------------------------


def test_pip_packages_command_is_pip_freeze_all():
    """The baseline fact command, ``--all`` kept so pip, setuptools and wheel
    stay visible to an operation managing those names."""
    assert PipPackages.command() == "pip freeze --all"
    assert PipPackages.command(pip="pip3") == "pip3 freeze --all"
    # A plain virtualenv path needs no quoting and rides verbatim.
    assert (
        PipPackages.command(pip="/opt/venv/bin/pip") == "/opt/venv/bin/pip freeze --all"
    )


def test_pip_packages_command_shell_quotes_the_executable():
    """A metacharacter in the pip executable is quoted, so it never reaches
    the remote command unescaped."""
    hostile = "pip;rm -rf /"
    assert PipPackages.command(pip=hostile) == f"{shlex.quote(hostile)} freeze --all"


# --- Fact: real-transport parsing (the baseline's PIP_REGEX shapes) -----------

# A realistic ``pip freeze --all`` block: pinned rows (``name==version``, the
# baseline's ``PIP_REGEX`` shape), the distro-shipped tools ``--all`` brings
# into view, and a mixed-case dotted name the normalization must fold.
FREEZE_OUTPUT = """\
blinker==1.7.0
pip==24.0
requests==2.31.0
setuptools==68.1.2
Zope.Interface==6.1
"""

# The same rows in reemote's entry shape: sorted by name, the mixed-case
# dotted name normalized to its PEP 503 form.
FREEZE_FACT = [
    _entry("blinker", "1.7.0"),
    _entry("pip", "24.0"),
    _entry("requests", "2.31.0"),
    _entry("setuptools", "68.1.2"),
    _entry("zope-interface", "6.1"),
]


async def test_pip_packages_parses_freeze_rows():
    """Over the real-transport path every ``name==version`` row becomes one
    sorted entry, and the gathered command is the fact's own."""
    host = CannedShellHost(FREEZE_OUTPUT)
    assert await PipPackages.process(host) == FREEZE_FACT
    assert host.last_shell == PipPackages.command()


async def test_pip_packages_skips_unparsable_rows():
    """Editables (``-e ...``), direct references (``name @ url``), names the
    baseline regex rejects (a ``;`` is not a name character) and blank lines
    contribute nothing -- the rows pyinfra's ``PIP_REGEX`` skips are skipped
    here too."""
    output = """\
-e git+https://github.com/example/thing.git#egg=thing
thing @ file:///dist/thing.whl
bad;name==1.0
PySide6==21.0-Omega

"""
    assert await PipPackages.process(CannedShellHost(output)) == []


async def test_pip_packages_keeps_post_local_and_punctuation_versions():
    """The narrowed version group keeps ``.post`` suffixes, local-build
    ``+...``-free dashes and dotted numeric dates whole; the name group keeps
    ``_``, ``+`` and digits (``lib2to3``, ``zope_events``)."""
    output = """\
foo==1.0.0.post1
bar==2024.03.01
baz==1.2.3-2
lib2to3==3.0
zope_events==5.0
typing_extensions==4.9.0
"""
    assert await PipPackages.process(CannedShellHost(output)) == [
        _entry("bar", "2024.03.01"),
        _entry("baz", "1.2.3-2"),
        _entry("foo", "1.0.0.post1"),
        _entry("lib2to3", "3.0"),
        _entry("typing-extensions", "4.9.0"),
        _entry("zope-events", "5.0"),
    ]


async def test_pip_packages_normalizes_and_dedups_names():
    """PEP 503 declares ``-``, ``_`` and ``.`` equivalent and the name
    case-insensitive, so the three spellings of one package collapse into
    the single normalized entry seen last."""
    output = """\
Zope.Interface==5.0
zope_interface==5.1
ZOPE-INTERFACE==6.1
"""
    assert await PipPackages.process(CannedShellHost(output)) == [
        _entry("zope-interface", "6.1")
    ]


# --- Fact: SimulatedHost seam -------------------------------------------------


async def test_pip_packages_reads_the_simulated_seam():
    """A SimulatedHost answers from its normalized ``pip_packages`` store,
    bypassing the shell; the default empty store reads as ``[]``, like the
    baseline's ``default = dict`` for a bare environment."""
    host = SimulatedHost(name="h", pip_packages={"six": "1.16.0"})
    assert await PipPackages.process(host) == [_entry("six", "1.16.0")]
    assert await PipPackages.process(SimulatedHost(name="h")) == []


async def test_pip_packages_seam_returns_copies():
    """Mutating a returned entry never touches the simulated state, so the
    seam hands back freshly built dicts in sorted order."""
    host = SimulatedHost(name="h", pip_packages={"a": "1", "b": "2"})
    entries = await PipPackages.process(host)
    entries[0]["version"] = "tampered"
    assert await PipPackages.process(host) == [_entry("a", "1"), _entry("b", "2")]


async def test_pip_packages_threads_the_pip_argument_over_the_real_path():
    """The operation's ``pip`` option reaches the fact's command, so the
    freeze always reports the environment the commands mutate."""
    host = CannedShellHost("")
    await PipPackages.process(host, pip="/venv/bin/pip3")
    assert host.last_shell == "/venv/bin/pip3 freeze --all"


async def test_gather_fact_pip_packages_fans_out():
    """Across a simulated fleet each host answers from its own seam store,
    keyed by host name."""
    host1 = SimulatedHost(name="web1", pip_packages={"six": "1.16.0"})
    host2 = SimulatedHost(name="web2")
    answers = await gather_fact([host1, host2], PipPackages)
    assert answers == {
        "web1": [_entry("six", "1.16.0")],
        "web2": [],
    }


# --- Operation: command descriptors --------------------------------------------


def test_pip_install_command_renders_pip_install():
    """pyinfra's install command builder verbatim: ``pip install`` plus the
    package tokens, ``==`` pins included."""
    cmd = PipInstallCommand(pip="pip", packages=("six==1.16.0", "requests"))
    assert cmd.shell() == "pip install six==1.16.0 requests"


def test_pip_install_command_inserts_extra_args_after_the_verb():
    """``extra_install_args`` rides between ``install`` and the names --
    pyinfra's token order -- appended unquoted (the dnf ``extra_args``
    convention: a raw shell fragment the caller keeps safe)."""
    cmd = PipInstallCommand(
        pip="pip3",
        packages=("six",),
        extra_args="--index-url https://example/simple",
    )
    assert cmd.shell() == "pip3 install --index-url https://example/simple six"


def test_pip_upgrade_command_renders_install_with_upgrade():
    """pyinfra builds the upgrade command as the install command plus
    ``--upgrade`` -- so the flag lands after any extra install arguments."""
    cmd = PipUpgradeCommand(pip="pip", packages=("six",))
    assert cmd.shell() == "pip install --upgrade six"
    extra = PipUpgradeCommand(pip="pip3", packages=("six",), extra_args="--user")
    assert extra.shell() == "pip3 install --user --upgrade six"


def test_pip_remove_command_renders_uninstall_yes():
    """The ``--yes`` flag answers pip's confirmation prompt, the baseline's
    uninstall builder."""
    cmd = PipRemoveCommand(pip="pip", packages=("six", "requests"))
    assert cmd.shell() == "pip uninstall --yes six requests"


def test_pip_requirements_command_renders_both_directions():
    """``-r <file>`` on the install side (with ``--upgrade`` when ``latest``),
    on the uninstall side otherwise -- pyinfra's requirements commands."""
    install = PipRequirementsCommand(pip="pip", path="/tmp/reqs.txt")
    assert install.shell() == "pip install -r /tmp/reqs.txt"
    latest = PipRequirementsCommand(
        pip="pip", path="/tmp/reqs.txt", latest=True, extra_args="--user"
    )
    assert latest.shell() == "pip install --user --upgrade -r /tmp/reqs.txt"
    remove = PipRequirementsCommand(pip="pip3", path="/tmp/reqs.txt", present=False)
    assert remove.shell() == "pip3 uninstall --yes -r /tmp/reqs.txt"


def test_pip_package_commands_are_elevated_by_sudo():
    """Installing into system site-packages needs root, so the request's
    ``sudo=True`` prefixes the rendered shell (the run-as user comes from
    the inventory ``privilege``, never the operation)."""
    cmd = PipInstallCommand(pip="pip", packages=("six",))
    assert command_shell(cmd) == "pip install six"
    assert command_shell(cmd, sudo=True) == "sudo pip install six"
    remove = PipRemoveCommand(pip="pip", packages=("six",))
    assert command_shell(remove, su=True) == f"su -c {shlex.quote(remove.shell())}"


def test_pip_package_commands_shell_quote_the_names():
    """Package names, the requirements path and the pip executable are
    shell-quoted, so a metacharacter can never reach the remote shell
    unescaped."""
    package = "six;touch /tmp/pwned"
    quoted = shlex.quote(package)
    assert quoted != package
    assert (
        PipInstallCommand(pip="pip", packages=(package,)).shell()
        == f"pip install {quoted}"
    )
    assert (
        PipRemoveCommand(pip="pip", packages=(package,)).shell()
        == f"pip uninstall --yes {quoted}"
    )
    path = "/tmp/re qs;rm"
    assert (
        PipRequirementsCommand(pip="pip", path=path).shell()
        == f"pip install -r {shlex.quote(path)}"
    )
    assert (
        PipInstallCommand(pip="pip x", packages=()).shell()
        == "'pip x' install"
    )


# --- Operation: idempotence -----------------------------------------------------


async def test_pip_packages_installs_missing_packages():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in pip.packages(ctx, packages=("pyinfra", "six"))]
    assert ctx.changed
    assert ctx.reason == "2 package(s) to install"
    assert commands == [PipInstallCommand(pip="pip", packages=("pyinfra", "six"))]


async def test_pip_packages_no_change_when_present():
    host = SimulatedHost(name="h", pip_packages={"six": "1.16.0"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in pip.packages(ctx, packages=("six",))]
    assert not ctx.changed
    assert ctx.reason == "all packages present"  # recorded even when unchanged
    assert commands == []


async def test_pip_packages_accepts_a_bare_string():
    """pyinfra accepts ``packages="pyinfra"`` as shorthand for a one-item list."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in pip.packages(ctx, packages="pyinfra")]
    assert commands == [PipInstallCommand(pip="pip", packages=("pyinfra",))]


async def test_pip_packages_none_is_a_noop():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in pip.packages(ctx)]
    assert not ctx.changed
    assert ctx.reason == "no packages requested"
    assert commands == []


# --- Operation: version pins ----------------------------------------------------


async def test_pip_packages_version_pin_present_only_on_exact_version():
    """A pinned ``<pkg>==<version>`` is present only when that *exact*
    version is installed (``ensure_packages``' rule, apt's and apk's)."""
    host = SimulatedHost(name="h", pip_packages={"six": "1.16.0"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in pip.packages(ctx, packages=("six==1.16.0",))]
    assert not ctx.changed
    assert ctx.reason == "all packages present"
    assert commands == []


async def test_pip_packages_version_pin_installs_when_version_differs():
    host = SimulatedHost(name="h", pip_packages={"six": "1.16.0"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in pip.packages(ctx, packages=("six==1.17.0",))]
    assert ctx.changed
    assert commands == [PipInstallCommand(pip="pip", packages=("six==1.17.0",))]
    # The pin token rides through to the shell verbatim (pip's own spelling).
    assert commands[0].shell() == "pip install six==1.17.0"


async def test_pip_packages_pin_splits_on_the_equals_not_the_name():
    """The pin splits at ``==``, so a version that itself contains ``=``
    stays whole and a bare name containing none reads unpinned."""
    host = SimulatedHost(name="h", pip_packages={"foo": "1=2"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in pip.packages(ctx, packages=("foo==1=2",))]
    assert not ctx.changed
    assert commands == []


async def test_pip_packages_compares_at_pep503_normalized_names():
    """Requests and fact entries meet at the normalized name, so
    ``Zope_Interface==5.0`` is present as the freeze's ``Zope.Interface``
    (pyinfra lowercases only -- a ``_``/``.`` spelling would miss it)."""
    host = SimulatedHost(name="h", pip_packages={"zope-interface": "5.0"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in pip.packages(ctx, packages=("Zope_Interface==5.0",))
    ]
    assert not ctx.changed
    assert ctx.reason == "all packages present"
    assert commands == []


async def test_pip_packages_other_specifiers_are_name_only():
    """No ``packaging`` dependency: a ``>=`` spec is not interpreted, so the
    request is satisfied by the name being installed at any version."""
    host = SimulatedHost(name="h", pip_packages={"requests": "2.31.0"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in pip.packages(ctx, packages=("requests>=2.28",))
    ]
    assert not ctx.changed
    assert commands == []


# --- Operation: latest ------------------------------------------------------------


async def test_pip_packages_latest_upgrades_installed_unpinned_packages():
    """``latest`` upgrades present, unpinned packages with
    ``pip install --upgrade``; missing ones still install (the install
    command comes first) -- the baseline's legacy dict-format behavior."""
    host = SimulatedHost(name="h", pip_packages={"six": "1.16.0"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in pip.packages(ctx, packages=("six", "pyinfra"), latest=True)
    ]
    assert commands == [
        PipInstallCommand(pip="pip", packages=("pyinfra",)),
        PipUpgradeCommand(pip="pip", packages=("six",)),
    ]
    assert commands[1].shell() == "pip install --upgrade six"
    assert ctx.reason == "1 package(s) to install; 1 package(s) to upgrade"


async def test_pip_packages_latest_skips_version_pins():
    """A pinned ``<pkg>==<version>`` is never an upgrade candidate."""
    host = SimulatedHost(name="h", pip_packages={"six": "1.16.0"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in pip.packages(ctx, packages=("six==1.16.0",), latest=True)
    ]
    assert not ctx.changed
    assert ctx.reason == "all packages present"
    assert commands == []


# --- Operation: removal -------------------------------------------------------------


async def test_pip_packages_removes_installed_packages_as_bare_names():
    """The removal feeds pip *bare names* -- ``pip uninstall`` rejects
    ``==`` requirement tokens -- while the pinned request stays
    version-aware for the presence test."""
    host = SimulatedHost(name="h", pip_packages={"six": "1.16.0"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in pip.packages(
            ctx, packages=("six==1.16.0", "pyinfra"), present=False
        )
    ]
    assert ctx.changed
    assert ctx.reason == "1 package(s) to remove"
    assert commands == [PipRemoveCommand(pip="pip", packages=("six",))]
    assert commands[0].shell() == "pip uninstall --yes six"


async def test_pip_packages_remove_is_version_aware():
    """A pin naming a version that is not the installed one leaves the
    package alone -- pyinfra's ``match_any`` noop on the wrong version."""
    host = SimulatedHost(name="h", pip_packages={"six": "1.16.0"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in pip.packages(ctx, packages=("six==2.0.0",), present=False)
    ]
    assert not ctx.changed
    assert ctx.reason == "no packages to remove"
    assert commands == []


async def test_pip_packages_remove_noop_when_absent():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in pip.packages(ctx, packages=("six",), present=False)
    ]
    assert not ctx.changed
    assert ctx.reason == "no packages to remove"
    assert commands == []


# --- Operation: requirements (stateless) -----------------------------------------


async def test_pip_packages_requirements_always_yields():
    """The baseline's requirements direction is stateless: the command is
    yielded on every run, whatever the environment holds."""
    host = SimulatedHost(name="h", pip_packages={"six": "1.16.0"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in pip.packages(ctx, requirements="/tmp/reqs.txt")
    ]
    assert ctx.changed
    assert ctx.reason == "requirements file /tmp/reqs.txt to install"
    assert commands == [
        PipRequirementsCommand(pip="pip", path="/tmp/reqs.txt", present=True)
    ]
    assert commands[0].shell() == "pip install -r /tmp/reqs.txt"


async def test_pip_packages_requirements_latest_adds_upgrade():
    """``latest`` makes the requirements install pyinfra's
    ``upgrade_command ... -r`` form."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in pip.packages(
            ctx, requirements="/tmp/reqs.txt", latest=True
        )
    ]
    assert commands[0].shell() == "pip install --upgrade -r /tmp/reqs.txt"


async def test_pip_packages_requirements_removal_direction():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in pip.packages(
            ctx, requirements="/tmp/reqs.txt", present=False
        )
    ]
    assert ctx.changed
    assert ctx.reason == "requirements file /tmp/reqs.txt to remove"
    assert commands[0].shell() == "pip uninstall --yes -r /tmp/reqs.txt"


async def test_pip_packages_requirements_yielded_before_package_commands():
    """pyinfra emits the requirements command first, then the package
    diff; both directions share one reason line."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in pip.packages(
            ctx, packages=("six",), requirements="/tmp/reqs.txt"
        )
    ]
    assert commands == [
        PipRequirementsCommand(pip="pip", path="/tmp/reqs.txt", present=True),
        PipInstallCommand(pip="pip", packages=("six",)),
    ]
    assert ctx.reason == (
        "requirements file /tmp/reqs.txt to install; 1 package(s) to install"
    )


# --- Operation: options threading and kwargs ---------------------------------------


async def test_pip_packages_threads_pip_and_extra_args_into_every_command():
    """``pip`` selects the executable for every command; the extra install
    arguments reach the install, upgrade and requirements commands."""
    host = CannedShellHost("six==1.16.0\n")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in pip.packages(
            ctx,
            packages=("six", "pyinfra"),
            requirements="/tmp/reqs.txt",
            latest=True,
            pip="/venv/bin/pip",
            extra_install_args="--index-url https://example/simple",
        )
    ]
    assert commands[0].shell() == (
        "/venv/bin/pip install --index-url https://example/simple --upgrade "
        "-r /tmp/reqs.txt"
    )
    assert commands[1].shell() == (
        "/venv/bin/pip install --index-url https://example/simple pyinfra"
    )
    assert commands[2].shell() == (
        "/venv/bin/pip install --index-url https://example/simple --upgrade six"
    )
    # The fact read rode the same executable.
    assert host.last_shell == "/venv/bin/pip freeze --all"


async def test_pip_packages_absorbs_the_dropped_virtualenv_options():
    """pyinfra's ``virtualenv``/``virtualenv_kwargs`` are deliberately
    dropped -- a dict would break the hashable-request rule at ``op()``, and
    venv creation delegates to the unmigrated ``pip.virtualenv``; a venv is
    targeted via ``pip=<venv>/bin/pip`` instead. Both keywords are absorbed
    by ``**kwargs`` with no effect, like the rest of the baseline globals."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in pip.packages(
            ctx,
            packages=("six",),
            future_flag=True,
            clean=False,
        )
    ]
    assert commands == [PipInstallCommand(pip="pip", packages=("six",))]


# --- Operation: SimulatedHost seam dispatch ----------------------------------------


async def test_pip_install_seam_records_versions():
    """``send_command`` maps ``PipInstallCommand`` onto ``pip_packages``: a
    pin records exactly that version -- OVERWRITING an older one, pip's
    re-pin behavior -- and an unpinned new name gets the ``""``
    placeholder while an installed name keeps its version."""
    host = SimulatedHost(name="h", pip_packages={"six": "1.16.0"})
    await send_command(
        host, PipInstallCommand(pip="pip", packages=("six==2.0.0", "pyinfra"))
    )
    assert host.pip_packages == {"six": "2.0.0", "pyinfra": ""}
    await send_command(host, PipInstallCommand(pip="pip", packages=("pyinfra",)))
    assert host.pip_packages == {"six": "2.0.0", "pyinfra": ""}


async def test_pip_install_seam_normalizes_the_name():
    """The store is keyed at the PEP 503 form, as the fact keys its answer,
    so a dotted request lands on the normalized entry."""
    host = SimulatedHost(name="h")
    await send_command(
        host, PipInstallCommand(pip="pip", packages=("Zope.Interface==6.1",))
    )
    assert host.pip_packages == {"zope-interface": "6.1"}


async def test_pip_upgrade_seam_leaves_versions():
    """The ``latest``-upgrade path has no index metadata to resolve newer
    versions against, so the simulated upgrade leaves versions untouched."""
    host = SimulatedHost(name="h", pip_packages={"six": "1.16.0"})
    await send_command(host, PipUpgradeCommand(pip="pip", packages=("six",)))
    assert host.pip_packages == {"six": "1.16.0"}


async def test_pip_remove_seam_drops_the_name():
    """Removal pops the normalized entry so ``PipPackages`` no longer
    reports it; an absent name is tolerated."""
    host = SimulatedHost(name="h", pip_packages={"six": "1.16.0"})
    await send_command(host, PipRemoveCommand(pip="pip", packages=("SIX",)))
    assert host.pip_packages == {}
    await send_command(host, PipRemoveCommand(pip="pip", packages=("absent",)))


async def test_pip_requirements_seam_applies_the_file():
    """The requirements descriptor reads the file's text from the shared
    ``remote_files`` store -- pins record versions, bare names the
    placeholder, comments and blank lines skip -- and an unseeded path
    applies nothing."""
    host = SimulatedHost(name="h")
    host.remote_files["/tmp/reqs.txt"] = (
        b"six==1.16.0  # pinned\npyinfra\n\n# a comment\n"
    )
    await send_command(
        host, PipRequirementsCommand(pip="pip", path="/tmp/reqs.txt")
    )
    assert host.pip_packages == {"six": "1.16.0", "pyinfra": ""}
    host.remote_files["/tmp/gone.txt"] = b""  # absent-style empty
    await send_command(
        host, PipRequirementsCommand(pip="pip", path="/tmp/missing.txt")
    )
    assert host.pip_packages == {"six": "1.16.0", "pyinfra": ""}


async def test_pip_requirements_seam_removal_direction():
    host = SimulatedHost(name="h", pip_packages={"six": "1.16.0"})
    host.remote_files["/tmp/reqs.txt"] = b"six==1.16.0\n"
    await send_command(
        host, PipRequirementsCommand(pip="pip", path="/tmp/reqs.txt", present=False)
    )
    assert host.pip_packages == {}


# --- Operation: end to end -----------------------------------------------------------


async def test_pip_packages_end_to_end_install_then_noop():
    """Through run_prepare/run_apply: the pinned install executes under sudo
    and renders in the log; a second prepare sees the version installed and
    plans no change (the fresh per-step fact cache observes the mutation)."""

    def install_deploy(host):
        yield op(
            pip.packages,
            name="Install six",
            packages=("six==1.16.0",),
            sudo=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], install_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (
        PipInstallCommand(pip="pip", packages=("six==1.16.0",)),
    )
    assert "$ sudo pip install six==1.16.0" in format_log(report)
    assert host.pip_packages == {"six": "1.16.0"}
    _runtimes, plans2 = await run_prepare([host], install_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "all packages present"


async def test_pip_packages_end_to_end_install_then_remove_cycle():
    """One deploy installs then removes: the apply log shows both shells,
    and the second request -- a fresh step with its own fact cache -- sees
    the install's mutation and finds the package to remove."""

    def cycle_deploy(host):
        yield op(pip.packages, name="Install six", packages=("six==1.16.0",))
        yield op(
            pip.packages,
            name="Remove six",
            packages=("six==1.16.0",),
            present=False,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], cycle_deploy)
    # Prepare reads pre-deployment state only: the install is planned as a
    # change, but the removal sees the still-empty environment and plans
    # nothing -- pyinfra's sequential- facts would have seen the install.
    assert plans["h"][0].changed
    assert not plans["h"][1].changed
    assert plans["h"][1].reason == "no packages to remove"
    report = await run_apply(runtimes, plans=plans)
    log = format_log(report)
    assert "$ pip install six==1.16.0" in log
    # The removal re-materializes against the mutated state and does run.
    assert "$ pip uninstall --yes six" in log
    assert host.pip_packages == {}


async def test_pip_packages_dry_run_plans_without_sending():
    """``dry=True`` stops after prepare: the install is planned as a change
    but there is no apply report, so nothing runs on the host."""

    def install_deploy(host):
        yield op(pip.packages, name="Install six", packages=("six",), sudo=True)

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        install_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert planned.commands == (PipInstallCommand(pip="pip", packages=("six",)),)
    # Nothing ran: the dry run sent no command.
    assert host.pip_packages == {}
