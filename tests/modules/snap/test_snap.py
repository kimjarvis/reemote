# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the snap module: its facts and the transport paths.

Covers ``reemote5.modules.snap.snap.SnapPackage`` -- one installed snap
package's identity parsed from the labelled ``snap info`` rows -- and
``reemote5.modules.snap.snap.SnapPackages``, the bare ``snap list`` names of
every installed snap. Each check runs both over the real-transport path (a
``CannedShellHost`` returning pyinfra's own ``snap.SnapPackage`` /
``snap.SnapPackages`` fixture output verbatim) and through the
``SimulatedHost`` seam, plus the ``gather_fact`` fan-out (which forwards the
package argument to every host only for ``SnapPackage``).

The ``snap.package`` operation -- a pyinfra port reading ``SnapPackage`` once
per requested snap -- is exercised over its three command descriptors
(``SnapInstallCommand`` / ``SnapRefreshCommand`` / ``SnapRemoveCommand``),
their rendering (pyinfra's own ``snap.package`` fixture commands verbatim),
elevation and shell quoting, its idempotence in both directions, the channel
switch, the ``classic`` confinement flag, the string/None normalization, the
dropped pyinfra globals absorbed by ``**kwargs``, the ``SimulatedHost`` seam
dispatch, and end-to-end through ``run`` / ``run_prepare`` + ``run_apply``
(including the ``sudo`` elevation and a dry run).

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
from reemote5.modules import snap
from reemote5.modules.snap.snap import (
    SnapInstallCommand,
    SnapPackage,
    SnapPackageInfo,
    SnapPackages,
    SnapRefreshCommand,
    SnapRemoveCommand,
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


def _installed(name: str, channel: str = "latest/stable") -> SnapPackageInfo:
    """One installed snap's ``SnapPackageInfo`` seeded into the
    ``snap_package_info`` seam store, spelled once per ``snap.package``
    operation test. Only the tracked ``channel`` the operation compares is
    given meaning; the store detail carries the empty-string placeholders an
    offline simulation has no store metadata to fill."""
    return {
        "name": name,
        "publisher": "",
        "snap_id": "",
        "channel": channel,
        "version": "",
    }


# --- Command parity -----------------------------------------------------------


def test_snap_package_command_guards_and_quotes():
    """The real-transport command is ``snap info`` of the shell-quoted package
    behind a ``2>/dev/null || true`` guard: pyinfra's ``requires_command``
    gating and empty-dict-on-absent answer are deliberately dropped so an
    absent package (or a host without ``snap``) reads as ``None``."""
    command = SnapPackage.command("lxd")
    assert command == "snap info lxd 2>/dev/null || true"


def test_snap_package_command_shell_quotes_the_name():
    """A metacharacter in a package name is shell-quoted, so it never reaches
    the remote command unescaped."""
    package = "lxd;touch /tmp/pwned"
    command = SnapPackage.command(package)
    assert command == "snap info 'lxd;touch /tmp/pwned' 2>/dev/null || true"
    assert command != f"snap info {package} 2>/dev/null || true"


# --- Real-transport parity (pyinfra's snap.SnapPackage fixtures) --------------

# The ``output`` field of pyinfra's ``snap.SnapPackage/lxd.json`` fixture,
# verbatim. The indented ``channels:`` table, the multi-line ``description``
# body and the ``commands:``/``services:`` lists must never be mistaken for a
# tracked top-level field row.
LXD_OUTPUT = """\
name:      lxd
summary:   LXD - container and VM manager
publisher: Canonical✓
store-url: https://snapcraft.io/lxd
contact:   lxc-devel@lists.linuxcontainers.org
license:   unset
description: |
  LXD is a system container and virtual machine manager.

  It offers a simple CLI and REST API to manage local or remote instances,
  uses an image based workflow and support for a variety of advanced features.
commands:
  - lxd.benchmark
  - lxd.buginfo
  - lxd.check-kernel
  - lxd.lxc
  - lxd.lxc-to-lxd
  - lxd
  - lxd.migrate
services:
  lxd.activate: oneshot, enabled, inactive
  lxd.daemon:   simple, enabled, active
snap-id:      J60k4JY0HppjwOjW8dZdYc8obXKxujRu
tracking:     4.0/stable/ubuntu-20.04
refresh-date: 17 days ago, at 23:00 UTC
channels:
  latest/stable:    5.6-794016a   2022-09-27 (23680) 142MB -
  latest/edge:      git-a0ed441   2022-10-01 (23729) 142MB -
  4.0/stable:       4.0.9-8e2046b 2022-03-26 (22753)  71MB -
installed:          4.0.9-8e2046b            (22753)  71MB -"""

# pyinfra's documented ``fact`` for the lxd fixture, in reemote5's entry shape:
# the hyphenated ``snap-id`` key becomes the ``snap_id`` field, the ``tracking``
# row is narrowed to ``<track>/<risk>`` and the ``installed`` row to the version
# token.
LXD_FACT: SnapPackageInfo = {
    "name": "lxd",
    "publisher": "Canonical✓",
    "snap_id": "J60k4JY0HppjwOjW8dZdYc8obXKxujRu",
    "channel": "4.0/stable",
    "version": "4.0.9-8e2046b",
}

# The ``output`` of pyinfra's ``snap.SnapPackage/snapd.json`` (a ``latest/stable``
# tracking with no branch) and the matching fact subset.
SNAPD_OUTPUT = """\
name:      snapd
summary:   Daemon and tooling that enable snap packages
publisher: Canonical**
store-url: https://snapcraft.io/snapd
license:   GPL-3.0
description: |
  Install, configure, refresh and remove snap packages. Snaps are
  'universal' packages that work across many different Linux systems.
type:         snapd
snap-id:      PMrrV4ml8uWuEUDBT8dSGnKUYbevVhc4
tracking:     latest/stable
refresh-date: today at 17:27 UTC
channels:
  latest/stable:    2.57.2                 2022-09-27 (17029) 50MB -
  latest/edge:      2.57.4+git586.g707c153 2022-10-01 (17380) 50MB -
installed:          2.57.2                            (17029) 50MB snapd"""

SNAPD_FACT: SnapPackageInfo = {
    "name": "snapd",
    "publisher": "Canonical**",
    "snap_id": "PMrrV4ml8uWuEUDBT8dSGnKUYbevVhc4",
    "channel": "latest/stable",
    "version": "2.57.2",
}


async def test_snap_package_parses_the_lxd_fixture():
    """Over the real-transport path, ``SnapPackage`` parses the lxd fixture's
    ``snap info`` rows into pyinfra's documented fact (with the ``snap_id``
    key rename), ignoring the indented ``channels`` table and the multi-line
    description body."""
    host = CannedShellHost(LXD_OUTPUT)
    assert await SnapPackage.process(host, "lxd") == LXD_FACT
    assert host.last_shell == SnapPackage.command("lxd")


async def test_snap_package_parses_the_snapd_fixture():
    """A ``latest/stable`` tracking (no branch to drop) and a version with no
    commit suffix parse exactly as pyinfra documents."""
    host = CannedShellHost(SNAPD_OUTPUT)
    assert await SnapPackage.process(host, "snapd") == SNAPD_FACT


# --- Absent package reads as None --------------------------------------------


async def test_snap_package_absent_reads_as_none():
    """An absent package prints no tracked row (in the real guarded command the
    ``error:`` line goes to stderr and stdout is empty), so the fact reports
    ``None`` rather than pyinfra's empty ``{}``. Both the empty-stdout case and
    pyinfra's not-found error line parse to ``None``."""
    assert await SnapPackage.process(CannedShellHost(""), "foo") is None
    error = 'error: no snap found for "foo"'
    assert await SnapPackage.process(CannedShellHost(error), "foo") is None


# --- SimulatedHost seam -------------------------------------------------------


async def test_snap_package_reads_the_simulated_seam():
    """A SimulatedHost answers from its package-keyed ``snap_package_info``
    store, bypassing the shell; a name absent from it reads as ``None`` -- an
    absent package -- and so does the default empty store."""
    host = SimulatedHost(name="h", snap_package_info={"lxd": LXD_FACT})
    assert await SnapPackage.process(host, "lxd") == LXD_FACT
    assert await SnapPackage.process(host, "snapd") is None
    assert await SnapPackage.process(SimulatedHost(name="h"), "lxd") is None


async def test_snap_package_seam_returns_copies():
    """The seam hands back a fresh dict, so mutating the result never touches
    the simulated state."""
    host = SimulatedHost(name="h", snap_package_info={"lxd": LXD_FACT})
    info = await SnapPackage.process(host, "lxd")
    info["version"] = "mutated"
    assert await SnapPackage.process(host, "lxd") == LXD_FACT


# --- gather_fact fan-out ------------------------------------------------------


async def test_gather_fact_snap_package_reads_simulated_seams():
    """Across a simulated fleet the package argument reaches every host's seam;
    each answers from its own store, keyed by host name."""
    host1 = SimulatedHost(name="web1", snap_package_info={"lxd": LXD_FACT})
    host2 = SimulatedHost(name="web2")
    infos = await gather_fact([host1, host2], SnapPackage, "lxd")
    assert infos == {"web1": LXD_FACT, "web2": None}


async def test_gather_fact_snap_package_over_canned_shells():
    """The fan-out drives every host's real-transport path with the quoted
    per-package command and keys the parsed results by host name."""
    host1 = CannedShellHost(LXD_OUTPUT)
    host1.name = "web1"
    host2 = CannedShellHost("")
    host2.name = "web2"
    infos = await gather_fact([host1, host2], SnapPackage, "lxd")
    assert infos == {"web1": LXD_FACT, "web2": None}
    assert host1.last_shell == SnapPackage.command("lxd")
    assert host2.last_shell == SnapPackage.command("lxd")


# =============================================================================
# SnapPackages -- the bare ``snap list`` names of every installed snap
# =============================================================================

# The ``output`` field of pyinfra's ``snap.SnapPackages/packages.json``
# fixture verbatim: the ``Name Version Rev Tracking Publisher Notes`` header
# followed by five installed snaps (the ``lxd`` row's Tracking column carries
# a trailing ``…`` and several Publisher cells a ``✓`` -- neither touches the
# leading ``Name`` token this fact keeps).
PACKAGES_OUTPUT = """\
Name              Version        Rev    Tracking         Publisher     Notes
core              16-2.57.2      13886  latest/stable    canonical✓    core
core18            20220831       2566   latest/stable    canonical✓    base
core20            20220826       1623   latest/stable    canonical✓    base
lxd               4.0.9-8e2046b  22753  4.0/stable/…     canonical✓    -
snapd             2.57.2         17029  latest/stable    canonical✓    snapd"""

# The fixture's ``fact`` field: the header discarded, each row's first token,
# in listing order.
PACKAGES_NAMES = ["core", "core18", "core20", "lxd", "snapd"]


# --- Command parity -----------------------------------------------------------


def test_snap_packages_command_guards_the_listing():
    """The real-transport command is pyinfra's ``snap list`` behind a
    ``2>/dev/null || true`` guard: pyinfra's ``requires_command`` gating on
    ``snap`` is deliberately dropped so a host without the tool reads as an
    empty name list rather than raising ``CommandNotFoundError``."""
    assert SnapPackages.COMMAND == "snap list 2>/dev/null || true"


# --- Real-transport parity (pyinfra's snap.SnapPackages fixture) --------------


async def test_snap_packages_parses_the_packages_fixture():
    """Over the real-transport path, ``SnapPackages`` drops the ``snap list``
    header and returns each row's leading ``Name`` token in listing order --
    pyinfra's documented fact -- ignoring the Version/Rev/Tracking/Publisher/
    Notes columns entirely."""
    host = CannedShellHost(PACKAGES_OUTPUT)
    assert await SnapPackages.process(host) == PACKAGES_NAMES
    assert host.last_shell == SnapPackages.COMMAND


async def test_snap_packages_skips_blanks_and_dedups():
    """A blank body line -- which pyinfra's bare ``split()[0]`` would raise on
    -- is skipped, a repeated name is kept once at its first position, and a
    row's surrounding whitespace never leaks into the token."""
    output = (
        "Name  Version  Rev  Tracking  Publisher  Notes\n"
        "core  1  1  latest/stable  canonical  core\n"
        "\n"
        "core  1  1  latest/stable  canonical  core\n"
        "   lxd  4  22753  4.0/stable  canonical  -"
    )
    assert await SnapPackages.process(CannedShellHost(output)) == ["core", "lxd"]


async def test_snap_packages_empty_reads_as_empty_list():
    """Nothing installed -- the header-only listing, or the guarded command on
    a host without ``snap`` (empty stdout) -- reads as ``[]``, the same empty
    answer pyinfra's ``default = list`` gives."""
    assert await SnapPackages.process(CannedShellHost("")) == []
    header_only = "Name  Version  Rev  Tracking  Publisher  Notes"
    assert await SnapPackages.process(CannedShellHost(header_only)) == []


# --- SimulatedHost seam (projected from the shared snap_package_info store) ---


async def test_snap_packages_projects_the_simulated_seam():
    """A SimulatedHost answers from the *same* ``snap_package_info`` store
    ``SnapPackage`` reads: the fact projects its keys -- the installed names --
    so the two snap facts never disagree; an empty store reads as ``[]``."""
    host = SimulatedHost(
        name="h", snap_package_info={"lxd": LXD_FACT, "snapd": SNAPD_FACT}
    )
    assert await SnapPackages.process(host) == ["lxd", "snapd"]
    assert await SnapPackages.process(SimulatedHost(name="h")) == []


async def test_snap_packages_seam_returns_copies():
    """The projected list is a fresh copy, so appending to the result never
    mutates the simulated store (the key set stays put)."""
    host = SimulatedHost(name="h", snap_package_info={"lxd": LXD_FACT})
    names = await SnapPackages.process(host)
    names.append("mutated")
    assert await SnapPackages.process(host) == ["lxd"]
    assert "mutated" not in host.snap_package_info


# --- gather_fact fan-out ------------------------------------------------------


async def test_gather_fact_snap_packages_reads_simulated_seams():
    """Across a simulated fleet each host projects its own store's keys, keyed
    by host name; an empty host answers ``[]``."""
    host1 = SimulatedHost(
        name="web1", snap_package_info={"lxd": LXD_FACT, "snapd": SNAPD_FACT}
    )
    host2 = SimulatedHost(name="web2")
    names = await gather_fact([host1, host2], SnapPackages)
    assert names == {"web1": ["lxd", "snapd"], "web2": []}


async def test_gather_fact_snap_packages_over_canned_shells():
    """The fan-out drives every host's real-transport path with the guarded
    ``snap list`` and keys the parsed name lists by host name."""
    host1 = CannedShellHost(PACKAGES_OUTPUT)
    host1.name = "web1"
    host2 = CannedShellHost("")
    host2.name = "web2"
    names = await gather_fact([host1, host2], SnapPackages)
    assert names == {"web1": PACKAGES_NAMES, "web2": []}
    assert host1.last_shell == SnapPackages.COMMAND
    assert host2.last_shell == SnapPackages.COMMAND


# =============================================================================
# snap.package -- the operation
# =============================================================================


# --- Command descriptors: rendering (pyinfra's snap.package fixtures) --------


def test_snap_install_command_renders_snap_install():
    """No flag sits between the verb and the names and the channel is last --
    pyinfra's ``snap install vlc --channel=latest/stable`` fixture command
    verbatim."""
    cmd = SnapInstallCommand(packages=("vlc",))
    assert cmd.shell() == "snap install vlc --channel=latest/stable"


def test_snap_install_command_places_classic_before_the_names():
    """``--classic`` rides between the verb and the names, as pyinfra renders
    it (``snap install --classic nvim --channel=latest/stable``)."""
    cmd = SnapInstallCommand(packages=("nvim",), classic=True)
    assert cmd.shell() == "snap install --classic nvim --channel=latest/stable"


def test_snap_install_command_carries_the_requested_channel():
    """A non-default channel installs on it verbatim (pyinfra's
    ``snap install lxd --channel=4.0/stable``)."""
    cmd = SnapInstallCommand(packages=("lxd",), channel="4.0/stable")
    assert cmd.shell() == "snap install lxd --channel=4.0/stable"


def test_snap_install_command_joins_multiple_names():
    """Several missing snaps share one install command, in request order."""
    cmd = SnapInstallCommand(packages=("vlc", "hello-world"))
    assert cmd.shell() == "snap install vlc hello-world --channel=latest/stable"


def test_snap_remove_command_renders_bare_snap_remove():
    """Removing takes no channel or confinement flag -- pyinfra's
    ``snap remove vlc hello-world``."""
    cmd = SnapRemoveCommand(packages=("vlc", "hello-world"))
    assert cmd.shell() == "snap remove vlc hello-world"


def test_snap_refresh_command_renders_snap_refresh_channel():
    """The channel switch is snap's own verb with the channel option --
    pyinfra's ``snap refresh lxd --channel=latest/edge``."""
    cmd = SnapRefreshCommand(packages=("lxd",), channel="latest/edge")
    assert cmd.shell() == "snap refresh lxd --channel=latest/edge"


# --- Command descriptors: elevation and quoting ------------------------------


def test_snap_commands_are_elevated_by_sudo():
    """Installing/removing/refreshing needs root, so the request's
    ``sudo=True`` prefixes the rendered shell (the run-as user comes from the
    inventory ``privilege``, never the operation)."""
    cmd = SnapInstallCommand(packages=("vlc",))
    assert command_shell(cmd) == "snap install vlc --channel=latest/stable"
    assert (
        command_shell(cmd, sudo=True)
        == "sudo snap install vlc --channel=latest/stable"
    )
    refresh = SnapRefreshCommand(packages=("lxd",), channel="latest/edge")
    assert command_shell(refresh, su=True) == f"su -c {shlex.quote(refresh.shell())}"


def test_snap_commands_shell_quote_the_names():
    """Package names are shell-quoted, so a metacharacter in a name can never
    reach the remote shell unescaped."""
    package = "lxd;touch /tmp/pwned"
    quoted = shlex.quote(package)
    assert quoted != package
    install = SnapInstallCommand(packages=(package,)).shell()
    assert install == f"snap install {quoted} --channel=latest/stable"
    assert SnapRemoveCommand(packages=(package,)).shell() == f"snap remove {quoted}"
    refresh = SnapRefreshCommand(packages=(package,)).shell()
    assert refresh == f"snap refresh {quoted} --channel=latest/stable"


def test_snap_channel_is_quoted_but_a_plain_one_is_verbatim():
    """The channel is shell-quoted too: a normal ``4.0/stable`` quotes to
    itself and renders verbatim, a hostile one is escaped."""
    assert shlex.quote("4.0/stable") == "4.0/stable"
    hostile = "latest/stable;reboot"
    cmd = SnapInstallCommand(packages=("lxd",), channel=hostile)
    assert cmd.shell() == f"snap install lxd --channel={shlex.quote(hostile)}"
    assert shlex.quote(hostile) != hostile


# --- Idempotence (pyinfra parity) --------------------------------------------


async def test_snap_package_installs_missing_packages():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in snap.package(ctx, packages=("vlc", "not_present"))]
    assert ctx.changed
    assert ctx.reason == "2 package(s) to install"
    assert commands == [SnapInstallCommand(packages=("vlc", "not_present"))]


async def test_snap_package_excludes_installed_from_install():
    """A name the fact already reports at the requested channel drops out;
    only the missing ones are installed."""
    host = SimulatedHost(name="h", snap_package_info={"lxd": _installed("lxd")})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in snap.package(ctx, packages=("vlc", "lxd"))]
    assert ctx.changed
    assert ctx.reason == "1 package(s) to install"
    assert commands == [SnapInstallCommand(packages=("vlc",))]


async def test_snap_package_no_change_when_present():
    host = SimulatedHost(name="h", snap_package_info={"lxd": _installed("lxd")})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in snap.package(ctx, packages=("lxd",))]
    assert not ctx.changed
    assert ctx.reason == "all packages present"  # recorded even when unchanged
    assert commands == []


async def test_snap_package_accepts_a_bare_string():
    """pyinfra accepts ``packages="vlc"`` as shorthand for a one-item list."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in snap.package(ctx, packages="vlc")]
    assert commands == [SnapInstallCommand(packages=("vlc",))]


async def test_snap_package_none_is_a_noop():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in snap.package(ctx)]
    assert not ctx.changed
    assert ctx.reason == "no packages requested"
    assert commands == []


async def test_snap_package_refreshes_on_channel_mismatch():
    """An installed snap tracked on a different channel yields the channel
    switch, not a reinstall -- pyinfra's ``refresh_package.json``."""
    seed = {"lxd": _installed("lxd", "4.0/stable")}
    host = SimulatedHost(name="h", snap_package_info=seed)
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in snap.package(ctx, packages=("lxd",), channel="latest/edge")
    ]
    assert ctx.changed
    assert ctx.reason == "1 package(s) to refresh"
    assert commands == [SnapRefreshCommand(packages=("lxd",), channel="latest/edge")]


async def test_snap_package_installs_and_refreshes_in_pyinfra_order():
    """A mixed request yields the install first, then the refresh, and the
    reason lists both counts."""
    seed = {"lxd": _installed("lxd", "4.0/stable")}
    host = SimulatedHost(name="h", snap_package_info=seed)
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in snap.package(
            ctx, packages=("vlc", "lxd"), channel="latest/edge"
        )
    ]
    assert ctx.changed
    assert ctx.reason == "1 package(s) to install; 1 package(s) to refresh"
    assert commands == [
        SnapInstallCommand(packages=("vlc",), channel="latest/edge"),
        SnapRefreshCommand(packages=("lxd",), channel="latest/edge"),
    ]


async def test_snap_package_removes_installed_packages():
    seed = {"vlc": _installed("vlc"), "hello-world": _installed("hello-world")}
    host = SimulatedHost(name="h", snap_package_info=seed)
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in snap.package(
            ctx, packages=("vlc", "hello-world"), present=False
        )
    ]
    assert ctx.changed
    assert ctx.reason == "2 package(s) to remove"
    assert commands == [SnapRemoveCommand(packages=("vlc", "hello-world"))]


async def test_snap_package_remove_noop_when_absent():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in snap.package(ctx, packages=("vlc",), present=False)
    ]
    assert not ctx.changed
    assert ctx.reason == "no packages to remove"
    assert commands == []


async def test_snap_package_classic_does_not_affect_idempotence():
    """``classic`` changes only how an install runs, never the fact-backed
    presence decision, so a present snap is a no-op even with the flag set."""
    host = SimulatedHost(name="h", snap_package_info={"nvim": _installed("nvim")})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in snap.package(ctx, packages=("nvim",), classic=True)
    ]
    assert not ctx.changed
    assert ctx.reason == "all packages present"
    assert commands == []


async def test_snap_package_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors pyinfra's global-arguments slot; op() already
    strips the ``name``/``sudo``/``su``/``doas`` meta-parameters, the rest is
    ignored."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in snap.package(ctx, packages=("vlc",), future_flag=True)
    ]
    assert commands == [SnapInstallCommand(packages=("vlc",))]


# --- SimulatedHost seam dispatch ---------------------------------------------


async def test_snap_install_seam_records_the_channel():
    """``send_command`` maps ``SnapInstallCommand`` onto ``snap_package_info``:
    a new name gains an entry carrying the channel the command installed on."""
    host = SimulatedHost(name="h")
    await send_command(host, SnapInstallCommand(packages=("vlc",), channel="latest/edge"))
    assert host.snap_package_info["vlc"]["name"] == "vlc"
    assert host.snap_package_info["vlc"]["channel"] == "latest/edge"


async def test_snap_install_seam_keeps_existing_detail():
    """Re-installing a present name leaves its recorded detail untouched -- the
    mutator skips installed snaps, as ``snap`` leaves a present snap alone."""
    detail = {
        "name": "vlc",
        "publisher": "Canonical\u2713",
        "snap_id": "X",
        "channel": "4.0/stable",
        "version": "1.2",
    }
    host = SimulatedHost(name="h", snap_package_info={"vlc": detail})
    await send_command(host, SnapInstallCommand(packages=("vlc",), channel="latest/edge"))
    assert host.snap_package_info["vlc"] == detail


async def test_snap_remove_seam_drops_the_name():
    """Removal pops the entry so ``SnapPackage`` reports ``None`` afterwards;
    an absent name is tolerated."""
    host = SimulatedHost(name="h", snap_package_info={"vlc": _installed("vlc")})
    await send_command(host, SnapRemoveCommand(packages=("vlc",)))
    assert host.snap_package_info == {}
    await send_command(host, SnapRemoveCommand(packages=("absent",)))


async def test_snap_refresh_seam_switches_channel_without_aliasing():
    """The refresh REPLACES the entry with a channel-updated copy -- keeping
    the other fields -- and never mutates the seeded dict object in place, so
    a test's fixture is not aliased into host state."""
    detail = {
        "name": "lxd",
        "publisher": "Canonical\u2713",
        "snap_id": "J60k",
        "channel": "4.0/stable",
        "version": "4.0.9",
    }
    host = SimulatedHost(name="h", snap_package_info={"lxd": detail})
    await send_command(host, SnapRefreshCommand(packages=("lxd",), channel="latest/edge"))
    assert host.snap_package_info["lxd"]["channel"] == "latest/edge"
    assert host.snap_package_info["lxd"]["publisher"] == "Canonical\u2713"
    # The seeded object is untouched: the mutator wrote a fresh copy.
    assert detail["channel"] == "4.0/stable"


# --- End to end --------------------------------------------------------------


async def test_snap_package_end_to_end_install_then_noop():
    """Through run_prepare/run_apply: the install executes under sudo and
    renders in the log; a second prepare sees the name installed and plans no
    change (the fresh per-step fact cache observes the mutation)."""

    def install_deploy(host):
        yield op(snap.package, name="Install vlc", packages=("vlc",), sudo=True)

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], install_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (SnapInstallCommand(packages=("vlc",)),)
    assert "$ sudo snap install vlc --channel=latest/stable" in format_log(report)
    assert host.snap_package_info["vlc"]["channel"] == "latest/stable"
    _runtimes, plans2 = await run_prepare([host], install_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "all packages present"


async def test_snap_package_end_to_end_channel_switch_then_noop():
    """An installed snap tracked elsewhere is re-channeled under sudo; the
    rerun is a no-op once the store records the new channel."""

    def edge_deploy(host):
        yield op(
            snap.package,
            name="Track lxd edge",
            packages=("lxd",),
            channel="latest/edge",
            sudo=True,
        )

    seed = {"lxd": _installed("lxd", "4.0/stable")}
    host = SimulatedHost(name="h", snap_package_info=seed)
    runtimes, plans = await run_prepare([host], edge_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    assert "$ sudo snap refresh lxd --channel=latest/edge" in format_log(report)
    assert host.snap_package_info["lxd"]["channel"] == "latest/edge"
    _runtimes, plans2 = await run_prepare([host], edge_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "all packages present"


async def test_snap_package_end_to_end_remove_then_noop():
    """``present=False`` removes under sudo and empties the store; the rerun
    reports nothing left to remove."""

    def remove_deploy(host):
        yield op(
            snap.package,
            name="Remove vlc",
            packages=("vlc",),
            present=False,
            sudo=True,
        )

    host = SimulatedHost(name="h", snap_package_info={"vlc": _installed("vlc")})
    runtimes, plans = await run_prepare([host], remove_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    assert "$ sudo snap remove vlc" in format_log(report)
    assert host.snap_package_info == {}
    _runtimes, plans2 = await run_prepare([host], remove_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "no packages to remove"


async def test_snap_package_dry_run_plans_without_sending():
    """``dry=True`` stops after prepare: the install is planned as a change but
    there is no apply report, so nothing runs on the host."""

    def install_deploy(host):
        yield op(snap.package, name="Install vlc", packages=("vlc",), sudo=True)

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
    assert planned.commands == (SnapInstallCommand(packages=("vlc",)),)
    # Nothing ran: the dry run sent no command.
    assert host.snap_package_info == {}
