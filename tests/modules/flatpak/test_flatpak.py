# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the flatpak module: its facts, the operation and the transport paths.

Covers ``reemote5.modules.flatpak.flatpak.FlatpakPackage`` -- one installed
flatpak package's identity parsed from the indented ``flatpak info`` label
rows -- and ``reemote5.modules.flatpak.flatpak.FlatpakPackages``, the bare
``flatpak list --columns=application`` IDs of every installed flatpak. Each
check runs both over the real-transport path (a ``CannedShellHost`` returning
the baseline's own ``flatpak.FlatpakPackage`` / ``flatpak.FlatpakPackages`` fixture
output verbatim) and through the ``SimulatedHost`` seam, plus the
``gather_fact`` fan-out (which forwards the package argument to every host
only for ``FlatpakPackage``).

The ``flatpak.packages`` operation -- a baseline port reading
``FlatpakPackages`` once -- is exercised over its two command descriptors
(``FlatpakInstallCommand`` / ``FlatpakRemoveCommand``), their rendering
(the baseline's own ``flatpak.packages`` fixture commands verbatim), elevation and
shell quoting, its idempotence in both directions, the install remote, the
string/None normalization, the dropped the baseline globals absorbed by
``**kwargs``, the ``SimulatedHost`` seam dispatch, and end-to-end through
``run`` / ``run_prepare`` + ``run_apply`` (including the ``sudo`` elevation
and a dry run).

``asyncio_mode = "auto"`` lets the async tests run without decorators.
"""

import shlex

from reemote5.core import FactCache, OpContext, op
from reemote5.drivers import (
    command_shell,
    format_log,
    gather_fact,
    run,
    run_apply,
    run_prepare,
    send_command,
)
from reemote5.hosts import Host
from reemote5.modules import flatpak
from reemote5.modules.flatpak.flatpak import (
    FlatpakInstallCommand,
    FlatpakPackage,
    FlatpakPackageInfo,
    FlatpakPackages,
    FlatpakRemoveCommand,
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


# --- Command parity -----------------------------------------------------------


def test_flatpak_package_command_guards_and_quotes():
    """The real-transport command is ``flatpak info`` of the shell-quoted
    package behind a ``2>/dev/null || true`` guard: the baseline's
    ``requires_command`` gating and empty-dict-on-absent answer are
    deliberately dropped so an absent package (or a host without ``flatpak``)
    reads as ``None``."""
    command = FlatpakPackage.command("org.videolan.VLC")
    assert command == "flatpak info org.videolan.VLC 2>/dev/null || true"


def test_flatpak_package_command_shell_quotes_the_name():
    """A metacharacter in a package name is shell-quoted, so it never reaches
    the remote command unescaped."""
    package = "org.videolan.VLC;touch /tmp/pwned"
    command = FlatpakPackage.command(package)
    assert command == "flatpak info 'org.videolan.VLC;touch /tmp/pwned' 2>/dev/null || true"
    assert command != f"flatpak info {package} 2>/dev/null || true"


# --- Real-transport parity (the baseline's flatpak.FlatpakPackage fixtures) --------

# The ``output`` field of the baseline's ``flatpak.FlatpakPackage/vlc.json`` fixture,
# verbatim. Only the three indented ``ID:``/``Ref:``/``Version:`` rows are
# tracked; the unindented title line, the blank lines around it and every other
# label (``Arch``, ``Branch``, ``License``, ``Origin``, ``Collection``,
# ``Installation``, ``Installed``, ``Runtime``, ``Sdk`` and the commit block)
# must never be mistaken for one.
VLC_OUTPUT = """\

VLC - VLC media player, the open-source multimedia player

          ID: org.videolan.VLC
         Ref: app/org.videolan.VLC/x86_64/stable
        Arch: x86_64
      Branch: stable
     Version: 3.0.21
     License: GPL-2.0+
      Origin: flathub
  Collection: org.flathub.Stable
Installation: system
   Installed: 98.9 MB
     Runtime: org.kde.Platform/x86_64/5.15-23.08
         Sdk: org.kde.Sdk/x86_64/5.15-23.08

      Commit: 00fce8e80caa0b5a74e4fd8baeda39e5450f406e9992586a578ec991258c927c
      Parent: 4905cf9d41d96f96b967ea4150a7f5d06930158f125fd41603936ddcd006aaaa
     Subject: Update VLC to 3.0.21 and some deps (c6a6bb62)
        Date: 2024-06-09 13:21:31 +0000"""

# The baseline's documented ``fact`` for the vlc fixture, in reemote5's entry shape.
VLC_FACT: FlatpakPackageInfo = {
    "id": "org.videolan.VLC",
    "ref": "app/org.videolan.VLC/x86_64/stable",
    "version": "3.0.21",
}

# The ``output`` of the baseline's ``flatpak.FlatpakPackage/kodi.json`` and its
# ``fact``: the version carries a non-numeric suffix (``21.0-Omega``), which
# The baseline's ``[\\w\\d.-]+`` capture keeps whole.
KODI_OUTPUT = """\

Kodi - Ultimate entertainment center

          ID: tv.kodi.Kodi
         Ref: app/tv.kodi.Kodi/x86_64/stable
        Arch: x86_64
      Branch: stable
     Version: 21.0-Omega
     License: GPL-2.0-only GPL-2.0-or-later LGPL-2.1-or-later MIT BSD-3-Clause BSD-4-Clause
      Origin: flathub
  Collection: org.flathub.Stable
Installation: system
   Installed: 514.7 MB
     Runtime: org.freedesktop.Platform/x86_64/23.08
         Sdk: org.freedesktop.Sdk/x86_64/23.08

      Commit: 03934475318a3a17476eed0f73e2d8339194373109f324868fc22484cbd7276e
      Parent: cb0195ac49050d0e16c840244de4c99061453102a270f6f930f3186a25bcd562
     Subject: Update addons (787f64c4)
        Date: 2024-06-01 18:22:30 +0000"""

KODI_FACT: FlatpakPackageInfo = {
    "id": "tv.kodi.Kodi",
    "ref": "app/tv.kodi.Kodi/x86_64/stable",
    "version": "21.0-Omega",
}


async def test_flatpak_package_parses_the_vlc_fixture():
    """Over the real-transport path, ``FlatpakPackage`` parses the vlc
    fixture's ``flatpak info`` rows into the baseline's documented fact, ignoring
    the untracked labels and the commit block."""
    host = CannedShellHost(VLC_OUTPUT)
    assert await FlatpakPackage.process(host, "org.videolan.VLC") == VLC_FACT
    assert host.last_shell == FlatpakPackage.command("org.videolan.VLC")


async def test_flatpak_package_parses_the_kodi_fixture():
    """A version carrying a non-numeric suffix (``21.0-Omega``) is captured
    whole, exactly as the baseline's capture group does."""
    host = CannedShellHost(KODI_OUTPUT)
    assert await FlatpakPackage.process(host, "tv.kodi.Kodi") == KODI_FACT


async def test_flatpak_package_keeps_the_indented_rows_only():
    """The regexes anchor on each row's leading indentation, so the unindented
    title line contributes nothing; a runtime, which flatpak prints without a
    ``Version:`` row, keeps that field at its empty-string default rather than
    reporting the baseline's partial dict."""
    runtime = """\

Runtime org.freedesktop.Platform

          ID: org.freedesktop.Platform
         Ref: runtime/org.freedesktop.Platform/x86_64/23.08
        Arch: x86_64
      Branch: 23.08
      Origin: flathub
Installation: system"""
    assert await FlatpakPackage.process(CannedShellHost(runtime), "x") == {
        "id": "org.freedesktop.Platform",
        "ref": "runtime/org.freedesktop.Platform/x86_64/23.08",
        "version": "",
    }


# --- Absent package reads as None --------------------------------------------


async def test_flatpak_package_absent_reads_as_none():
    """An absent package prints no tracked row (in the real guarded command the
    ``error:`` line goes to stderr and stdout is empty), so the fact reports
    ``None`` rather than the baseline's empty ``{}``. Both the empty-stdout case and
    the baseline's not-found error line parse to ``None``."""
    assert await FlatpakPackage.process(CannedShellHost(""), "foo") is None
    error = "error: not_found.test.test/*unspecified*/*unspecified* not installed"
    assert await FlatpakPackage.process(CannedShellHost(error), "not_found.test.test") is None


# --- SimulatedHost seam -------------------------------------------------------


async def test_flatpak_package_reads_the_simulated_seam():
    """A SimulatedHost answers from its package-keyed ``flatpak_package_info``
    store, bypassing the shell; a name absent from it reads as ``None`` -- an
    absent package -- and so does the default empty store."""
    host = SimulatedHost(name="h", flatpak_package_info={"org.videolan.VLC": VLC_FACT})
    assert await FlatpakPackage.process(host, "org.videolan.VLC") == VLC_FACT
    assert await FlatpakPackage.process(host, "tv.kodi.Kodi") is None
    assert await FlatpakPackage.process(SimulatedHost(name="h"), "org.videolan.VLC") is None


async def test_flatpak_package_seam_returns_copies():
    """The seam hands back a fresh dict, so mutating the result never touches
    the simulated state."""
    host = SimulatedHost(name="h", flatpak_package_info={"org.videolan.VLC": VLC_FACT})
    info = await FlatpakPackage.process(host, "org.videolan.VLC")
    info["version"] = "mutated"
    assert await FlatpakPackage.process(host, "org.videolan.VLC") == VLC_FACT


# --- gather_fact fan-out ------------------------------------------------------


async def test_gather_fact_flatpak_package_reads_simulated_seams():
    """Across a simulated fleet the package argument reaches every host's seam;
    each answers from its own store, keyed by host name."""
    host1 = SimulatedHost(name="web1", flatpak_package_info={"org.videolan.VLC": VLC_FACT})
    host2 = SimulatedHost(name="web2")
    infos = await gather_fact([host1, host2], FlatpakPackage, "org.videolan.VLC")
    assert infos == {"web1": VLC_FACT, "web2": None}


async def test_gather_fact_flatpak_package_over_canned_shells():
    """The fan-out drives every host's real-transport path with the quoted
    per-package command and keys the parsed results by host name."""
    host1 = CannedShellHost(VLC_OUTPUT)
    host1.name = "web1"
    host2 = CannedShellHost("")
    host2.name = "web2"
    infos = await gather_fact([host1, host2], FlatpakPackage, "org.videolan.VLC")
    assert infos == {"web1": VLC_FACT, "web2": None}
    assert host1.last_shell == FlatpakPackage.command("org.videolan.VLC")
    assert host2.last_shell == FlatpakPackage.command("org.videolan.VLC")


# =============================================================================
# FlatpakPackages -- the bare ``flatpak list`` application IDs
# =============================================================================

# The ``output`` of the baseline's ``flatpak.FlatpakPackages/packages.json``
# fixture verbatim: the ``Application ID`` header followed by four installed
# applications.
PACKAGES_OUTPUT = """\
Application ID
com.bitwarden.desktop
com.mattermost.Desktop
org.videolan.VLC
tv.kodi.Kodi"""

# The fixture's ``fact``: the header discarded, every remaining row one
# dotted application ID, in listing order.
PACKAGES_IDS = [
    "com.bitwarden.desktop",
    "com.mattermost.Desktop",
    "org.videolan.VLC",
    "tv.kodi.Kodi",
]


# --- Command parity -----------------------------------------------------------


def test_flatpak_packages_command_guards_the_listing():
    """The real-transport command is the baseline's ``flatpak list
    --columns=application`` behind a ``2>/dev/null || true`` guard: the baseline's
    ``requires_command`` gating on ``flatpak`` is deliberately dropped so a
    host without the tool reads as an empty ID list rather than raising
    ``CommandNotFoundError``."""
    assert FlatpakPackages.COMMAND == (
        "flatpak list --columns=application 2>/dev/null || true"
    )


# --- Real-transport parity (the baseline's flatpak.FlatpakPackages fixtures) -------


async def test_flatpak_packages_parses_the_packages_fixture():
    """Over the real-transport path, ``FlatpakPackages`` drops the
    ``Application ID`` header and returns every remaining row whole --
    the baseline's documented fact."""
    host = CannedShellHost(PACKAGES_OUTPUT)
    assert await FlatpakPackages.process(host) == PACKAGES_IDS
    assert host.last_shell == FlatpakPackages.COMMAND


async def test_flatpak_packages_parses_the_no_header_fixture():
    """The baseline's second fixture prints no header line; every row
    contributes its ID, so the fact never mistakes a row for a header."""
    no_header = "\n".join(PACKAGES_IDS)
    assert await FlatpakPackages.process(CannedShellHost(no_header)) == PACKAGES_IDS


async def test_flatpak_packages_skips_blanks_and_dedups():
    """A blank body line -- which the baseline's bare row handling would keep --
    is skipped, a repeated ID is kept once at its first position, and a row's
    surrounding whitespace never leaks into the token."""
    output = "Application ID\norg.videolan.VLC\n\norg.videolan.VLC\n   tv.kodi.Kodi  "
    assert await FlatpakPackages.process(CannedShellHost(output)) == [
        "org.videolan.VLC",
        "tv.kodi.Kodi",
    ]


async def test_flatpak_packages_empty_reads_as_empty_list():
    """Nothing installed -- the header-only listing, or the guarded command
    on a host without ``flatpak`` (empty stdout) -- reads as ``[]``, the same
    empty answer the baseline's ``default = list`` gives."""
    assert await FlatpakPackages.process(CannedShellHost("")) == []
    assert await FlatpakPackages.process(CannedShellHost("Application ID")) == []


# --- SimulatedHost seam (projected from the shared flatpak_package_info store) ---


async def test_flatpak_packages_projects_the_simulated_seam():
    """A SimulatedHost answers from the *same* ``flatpak_package_info`` store
    ``FlatpakPackage`` reads: the fact projects its keys -- the installed
    application IDs -- so the two flatpak facts never disagree; an empty
    store reads as ``[]``."""
    host = SimulatedHost(
        name="h", flatpak_package_info={"org.videolan.VLC": VLC_FACT, "tv.kodi.Kodi": KODI_FACT}
    )
    assert await FlatpakPackages.process(host) == ["org.videolan.VLC", "tv.kodi.Kodi"]
    assert await FlatpakPackages.process(SimulatedHost(name="h")) == []


async def test_flatpak_packages_seam_returns_copies():
    """The projected list is a fresh copy, so appending to the result never
    mutates the simulated store (the key set stays put)."""
    host = SimulatedHost(name="h", flatpak_package_info={"org.videolan.VLC": VLC_FACT})
    ids = await FlatpakPackages.process(host)
    ids.append("mutated")
    assert await FlatpakPackages.process(host) == ["org.videolan.VLC"]
    assert "mutated" not in host.flatpak_package_info


# --- gather_fact fan-out ------------------------------------------------------


async def test_gather_fact_flatpak_packages_reads_simulated_seams():
    """Across a simulated fleet each host projects its own store's keys,
    keyed by host name; an empty host answers ``[]``."""
    host1 = SimulatedHost(name="web1", flatpak_package_info={"org.videolan.VLC": VLC_FACT})
    host2 = SimulatedHost(name="web2")
    ids = await gather_fact([host1, host2], FlatpakPackages)
    assert ids == {"web1": ["org.videolan.VLC"], "web2": []}


async def test_gather_fact_flatpak_packages_over_canned_shells():
    """The fan-out drives every host's real-transport path with the guarded
    ``flatpak list`` and keys the parsed ID lists by host name."""
    host1 = CannedShellHost(PACKAGES_OUTPUT)
    host1.name = "web1"
    host2 = CannedShellHost("")
    host2.name = "web2"
    ids = await gather_fact([host1, host2], FlatpakPackages)
    assert ids == {"web1": PACKAGES_IDS, "web2": []}
    assert host1.last_shell == FlatpakPackages.COMMAND
    assert host2.last_shell == FlatpakPackages.COMMAND


# =============================================================================
# flatpak.packages -- the operation
# =============================================================================


# --- Command descriptors: rendering (the baseline's flatpak.packages fixtures) -----


def test_flatpak_install_command_renders_bare_install():
    """No flag sits between the verb and the names and no remote token
    appears when none was given -- the baseline's ``install_package`` fixture
    command ``flatpak install --noninteractive org.videolan.VLC``."""
    cmd = FlatpakInstallCommand(packages=("org.videolan.VLC",))
    assert cmd.shell() == "flatpak install --noninteractive org.videolan.VLC"


def test_flatpak_install_command_places_the_remote_before_the_names():
    """The remote rides right after ``--noninteractive``, then the IDs --
    the baseline's token order for an install from e.g. flathub."""
    cmd = FlatpakInstallCommand(packages=("org.videolan.VLC",), remote="flathub")
    assert cmd.shell() == "flatpak install --noninteractive flathub org.videolan.VLC"


def test_flatpak_install_command_joins_multiple_names():
    """Several missing applications share one install command, in request
    order (the baseline's ``install_packages`` doc example)."""
    cmd = FlatpakInstallCommand(packages=("org.videolan.VLC", "tv.kodi.Kodi"))
    assert cmd.shell() == "flatpak install --noninteractive org.videolan.VLC tv.kodi.Kodi"


def test_flatpak_remove_command_renders_bare_uninstall():
    """Removing takes no remote -- the baseline's ``remove_packages`` fixture
    command ``flatpak uninstall --noninteractive org.videolan.VLC``."""
    cmd = FlatpakRemoveCommand(packages=("org.videolan.VLC",))
    assert cmd.shell() == "flatpak uninstall --noninteractive org.videolan.VLC"


# --- Command descriptors: elevation and quoting ------------------------------


def test_flatpak_commands_are_elevated_by_sudo():
    """Installing/removing needs root, so the request's ``sudo=True``
    prefixes the rendered shell (the run-as user comes from the inventory
    ``privilege``, never the operation)."""
    cmd = FlatpakInstallCommand(packages=("org.videolan.VLC",))
    assert command_shell(cmd) == "flatpak install --noninteractive org.videolan.VLC"
    assert (
        command_shell(cmd, sudo=True)
        == "sudo flatpak install --noninteractive org.videolan.VLC"
    )
    remove = FlatpakRemoveCommand(packages=("org.videolan.VLC",))
    assert command_shell(remove, su=True) == f"su -c {shlex.quote(remove.shell())}"


def test_flatpak_install_command_shell_quotes_the_names():
    """The baseline's ``install_quotes_injection`` fixture verbatim: the metacharacter
    name is quoted, a plain remote renders unescaped."""
    cmd = FlatpakInstallCommand(packages=("x; reboot",), remote="flathub")
    assert cmd.shell() == "flatpak install --noninteractive flathub 'x; reboot'"
    hostile = "org.videolan.VLC;touch /tmp/pwned"
    assert (
        FlatpakRemoveCommand(packages=(hostile,)).shell()
        == f"flatpak uninstall --noninteractive {shlex.quote(hostile)}"
    )


# --- Idempotence (baseline parity) ---------------------------------------------


async def test_flatpak_packages_installs_missing_package():
    """The baseline's ``install_package`` fixture: nothing installed, one ID
    requested -- the bare install command."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in flatpak.packages(ctx, packages=("org.videolan.VLC",))]
    assert ctx.changed
    assert ctx.reason == "1 package(s) to install"
    assert commands == [FlatpakInstallCommand(packages=("org.videolan.VLC",))]


async def test_flatpak_packages_excludes_installed_from_install():
    """The baseline's ``install_packages`` fixture: a name the fact already lists
    drops out; only the missing one is installed."""
    host = SimulatedHost(name="h", flatpak_package_info={"org.videolan.VLC": VLC_FACT})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in flatpak.packages(
            ctx, packages=("org.videolan.VLC", "tv.kodi.Kodi")
        )
    ]
    assert ctx.changed
    assert ctx.reason == "1 package(s) to install"
    assert commands == [FlatpakInstallCommand(packages=("tv.kodi.Kodi",))]


async def test_flatpak_packages_no_change_when_present():
    host = SimulatedHost(name="h", flatpak_package_info={"org.videolan.VLC": VLC_FACT})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in flatpak.packages(ctx, packages=("org.videolan.VLC",))
    ]
    assert not ctx.changed
    assert ctx.reason == "all packages present"  # recorded even when unchanged
    assert commands == []


async def test_flatpak_packages_accepts_a_bare_string():
    """The baseline accepts ``packages="org.videolan.VLC"`` as shorthand for a
    one-item list."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in flatpak.packages(ctx, packages="org.videolan.VLC")
    ]
    assert commands == [FlatpakInstallCommand(packages=("org.videolan.VLC",))]


async def test_flatpak_packages_none_is_a_noop():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in flatpak.packages(ctx)]
    assert not ctx.changed
    assert ctx.reason == "no packages requested"
    assert commands == []


async def test_flatpak_packages_strips_an_empty_remote():
    """The baseline strips the remote and treats an empty one as absent
    (``if remote:``): the install command carries no remote token."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in flatpak.packages(
            ctx, packages=("org.videolan.VLC",), remote="   "
        )
    ]
    assert commands == [FlatpakInstallCommand(packages=("org.videolan.VLC",))]
    assert commands[0].shell() == "flatpak install --noninteractive org.videolan.VLC"


async def test_flatpak_packages_removes_installed_packages():
    """The baseline's ``remove_packages`` fixture: of the two requested names only
    the installed one is uninstalled -- the absent one is the baseline's
    ``flatpak package X is not installed`` noop, folded into the shared
    reason."""
    host = SimulatedHost(name="h", flatpak_package_info={"org.videolan.VLC": VLC_FACT})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in flatpak.packages(
            ctx, packages=("org.videolan.VLC", "tv.kodi.Kodi"), present=False
        )
    ]
    assert ctx.changed
    assert ctx.reason == "1 package(s) to remove"
    assert commands == [FlatpakRemoveCommand(packages=("org.videolan.VLC",))]


async def test_flatpak_packages_remove_noop_when_absent():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in flatpak.packages(ctx, packages=("org.videolan.VLC",), present=False)
    ]
    assert not ctx.changed
    assert ctx.reason == "no packages to remove"
    assert commands == []


async def test_flatpak_packages_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors the baseline's global-arguments slot; op() already
    strips the ``name``/``sudo``/``su``/``doas`` meta-parameters, the rest is
    ignored."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in flatpak.packages(ctx, packages=("org.videolan.VLC",), future_flag=True)
    ]
    assert commands == [FlatpakInstallCommand(packages=("org.videolan.VLC",))]


# --- SimulatedHost seam dispatch ----------------------------------------------


async def test_flatpak_install_seam_records_the_applications():
    """``send_command`` maps ``FlatpakInstallCommand`` onto
    ``flatpak_package_info``: a new name gains a placeholder entry and both
    flatpak facts see it -- the install remote is not modeled."""
    host = SimulatedHost(name="h")
    await send_command(
        host, FlatpakInstallCommand(packages=("org.videolan.VLC",), remote="flathub")
    )
    assert host.flatpak_package_info["org.videolan.VLC"] == {
        "id": "org.videolan.VLC",
        "ref": "",
        "version": "",
    }
    assert await FlatpakPackages.process(host) == ["org.videolan.VLC"]


async def test_flatpak_install_seam_keeps_existing_detail():
    """Re-installing a present name leaves its recorded detail untouched --
    the mutator skips installed applications, as ``flatpak install`` leaves a
    present application alone."""
    host = SimulatedHost(name="h", flatpak_package_info={"org.videolan.VLC": VLC_FACT})
    await send_command(host, FlatpakInstallCommand(packages=("org.videolan.VLC",)))
    assert host.flatpak_package_info["org.videolan.VLC"] == VLC_FACT


async def test_flatpak_remove_seam_drops_the_name():
    """Removal pops the entry so ``FlatpakPackage`` reports ``None`` and
    ``FlatpakPackages`` no longer lists it afterwards; an absent name is
    tolerated."""
    host = SimulatedHost(name="h", flatpak_package_info={"org.videolan.VLC": VLC_FACT})
    await send_command(host, FlatpakRemoveCommand(packages=("org.videolan.VLC",)))
    assert host.flatpak_package_info == {}
    await send_command(host, FlatpakRemoveCommand(packages=("absent",)))


# --- End to end ---------------------------------------------------------------


async def test_flatpak_packages_end_to_end_install_then_noop():
    """Through run_prepare/run_apply: the install executes under sudo and
    renders in the log; a second prepare sees the ID listed and plans no
    change (the fresh per-step fact cache observes the mutation)."""

    def install_deploy(host):
        yield op(
            flatpak.packages, name="Install vlc", packages=("org.videolan.VLC",), sudo=True
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], install_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (FlatpakInstallCommand(packages=("org.videolan.VLC",)),)
    assert "$ sudo flatpak install --noninteractive org.videolan.VLC" in format_log(report)
    assert host.flatpak_package_info["org.videolan.VLC"]["id"] == "org.videolan.VLC"
    _runtimes, plans2 = await run_prepare([host], install_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "all packages present"


async def test_flatpak_packages_end_to_end_remove_then_noop():
    """``present=False`` removes under sudo and empties the store; the rerun
    reports nothing left to remove."""

    def remove_deploy(host):
        yield op(
            flatpak.packages,
            name="Remove vlc",
            packages=("org.videolan.VLC",),
            present=False,
            sudo=True,
        )

    host = SimulatedHost(name="h", flatpak_package_info={"org.videolan.VLC": VLC_FACT})
    runtimes, plans = await run_prepare([host], remove_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    assert "$ sudo flatpak uninstall --noninteractive org.videolan.VLC" in format_log(report)
    assert host.flatpak_package_info == {}
    _runtimes, plans2 = await run_prepare([host], remove_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "no packages to remove"


async def test_flatpak_packages_dry_run_plans_without_sending():
    """``dry=True`` stops after prepare: the install is planned as a change
    but there is no apply report, so nothing runs on the host."""

    def install_deploy(host):
        yield op(
            flatpak.packages, name="Install vlc", packages=("org.videolan.VLC",), sudo=True
        )

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
    assert planned.commands == (FlatpakInstallCommand(packages=("org.videolan.VLC",)),)
    # Nothing ran: the dry run sent no command.
    assert host.flatpak_package_info == {}
