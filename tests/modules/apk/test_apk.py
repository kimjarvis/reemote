# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the apk module: ``ApkAutoremovable``, ``ApkExplicitPackages``,
``ApkHeldPackages``, ``ApkPackages`` and ``ApkRepositories``.

Covers the read-only state discovery in ``reemote5.modules.apk.apk``:
``ApkPackages`` is exercised both over the real-transport path (parsing
canned ``apk list --installed`` output, reusing pyinfra's own
``apk.ApkPackages`` fact fixtures verbatim -- the small
``packages_2.json`` fixture in full and a selected row set of the large
alpine ``packages.json`` fixture) and through the ``SimulatedHost`` seam,
plus the de-dup/sort and ``WARNING:`` noise-filtering edge cases and the
``gather_fact`` fan-out. ``ApkExplicitPackages`` and ``ApkRepositories`` --
reemote5 originals with no pyinfra fixtures -- are fed a realistic canned
``/etc/apk/world`` and ``/etc/apk/repositories`` file respectively, plus the
constraint-dropping, comment/conflict and malformed-line skips, the seam
copy semantics and their own fan-outs. ``ApkPackage`` -- a reemote5 original
querying one package's detail with no pyinfra fixture -- is fed a realistic
canned ``apk info -a`` block, plus the untracked-section skip, the scalar
absent-field defaults, the size coercion, the shell-quoting and None-on-
absent edges, the seam copy semantics and its package-argument fan-out.
``ApkAutoremovable`` -- the apk counterpart of ``apt.AptAutoremovable``,
also a reemote5 original with no pyinfra fixture -- is fed a realistic
canned ``apk add --purge --simulate`` block, plus the summary/footer skip,
the multi-dash and punctuation name preservation, the dedup+sort and the
seam copy semantics and fan-out.

The ``apk.packages`` operation -- a pyinfra port reading ``ApkPackages`` --
is exercised over its three command descriptors (``ApkInstallCommand`` /
``ApkRemoveCommand`` / ``ApkUpgradeCommand``), its idempotence and
version-pin matching, the ``latest`` upgrade path, the deliberately dropped
``update``/``upgrade`` options, the ``SimulatedHost`` seam dispatch, and
end-to-end through ``run`` / ``run_prepare`` + ``run_apply`` (including the
``sudo`` elevation and a dry run).

The ``apk.virtual`` operation -- a reemote5 original with no pyinfra
counterpart, managing a virtual package group through its single
present-flagged ``ApkVirtualCommand`` -- is exercised over the descriptor's
create/remove rendering and elevation, its world-name idempotence against
``ApkExplicitPackages`` in both directions, the string/None normalization and
the empty-packages no-op, the ``SimulatedHost`` seam dispatch, and
end-to-end through ``run_prepare`` + ``run_apply`` (create-then-noop, a
create-then-remove cycle, and a dry run).

The ``apk.hold`` operation -- the apk counterpart of ``apt.mark``, pinning
packages back from upgrades through a version constraint in ``/etc/apk/world``
-- is exercised over its single present-flagged ``ApkHoldCommand`` descriptor's
hold/unhold rendering and elevation, its version-aware idempotence against the
``ApkHeldPackages`` fact (name -> constraint) in both directions, the explicit
``version`` and pin-to-installed paths, the uninstalled-name skip, the
string/empty normalization and request-order preservation, the ``SimulatedHost``
seam dispatch, and end-to-end through ``run_prepare`` + ``run_apply``
(hold-then-noop, a hold-then-unhold cycle, and a dry run). ``ApkHeldPackages``
itself -- reading the same world file as ``ApkExplicitPackages`` but keeping
only the version-bound lines -- is fed the same canned ``/etc/apk/world``, plus
the tag-only and operator-form edges, the seam copy semantics and its fan-outs.

The ``apk.autoremove`` operation -- the apk counterpart of ``apt.auto_remove``,
purging orphan packages through its argument-less ``ApkPurgeCommand``
(``apk add --purge``) -- is exercised over the descriptor's rendering and
elevation, its idempotence against the ``ApkAutoremovable`` fact in both the
non-empty (yield) and empty (no-op) directions, the sorted-name reason string,
the ``**kwargs`` absorption, the ``SimulatedHost`` seam dispatch (clearing the
fact and popping the names from ``apk_packages`` while leaving the world and
the holds untouched), and end-to-end through ``run_prepare`` + ``run_apply``
(purge-then-noop, and a dry run).

``asyncio_mode = "auto"`` lets the async tests below run without decorators.
"""

import json
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
from reemote5.modules import apk
from reemote5.modules.apk.apk import (
    ApkAutoremovable,
    ApkExplicitPackages,
    ApkHeldPackages,
    ApkHoldCommand,
    ApkInstallCommand,
    ApkPackage,
    ApkPackages,
    ApkPurgeCommand,
    ApkRemoveCommand,
    ApkRepositories,
    ApkUpgradeCommand,
    ApkVirtualCommand,
)
from tests.simulated_host import SimulatedHost


class CannedShellHost(Host):
    """Returns fixed stdout from ``run_shell`` instead of connecting."""

    def __init__(self, stdout: str):
        super().__init__(name="canned")
        self._stdout = stdout
        self.last_shell: str | None = None

    async def run_shell(self, shell: str) -> str:
        self.last_shell = shell
        return self._stdout


# --- Command parity (pyinfra's apk.ApkPackages fixtures) ----------------------


def test_apk_packages_command_matches_pyinfra():
    """The command is pyinfra's fixture command verbatim: apk list of the
    installed packages (pyinfra's ``requires_command`` gating on ``apk`` is
    deliberately dropped)."""
    assert ApkPackages.COMMAND == "apk list --installed"


# --- Real-transport parity (pyinfra's apk.ApkPackages/packages_2.json) --------

# The ``output`` field of pyinfra's ``apk.ApkPackages/packages_2.json``
# fixture verbatim: two ``WARNING:`` index-noise lines followed by six
# installed packages on an old alpine host.
PACKAGES_2_OUTPUT = """\
WARNING: Ignoring APKINDEX.00740ba1.tar.gz: No such file or directory
WARNING: Ignoring APKINDEX.d8b2a6f4.tar.gz: No such file or directory
musl-1.1.22-r3 x86_64 {musl} (MIT) [installed]
zlib-1.2.11-r1 x86_64 {zlib} (zlib) [installed]
apk-tools-2.10.4-r2 x86_64 {apk-tools} (GPL2) [installed]
musl-utils-1.1.22-r3 x86_64 {musl} (MIT BSD GPL2+) [installed]
iproute2-5.17.0-r0 x86_64 {iproute2} (GPL-2.0-or-later) [installed]
linux-firmware-rtl_nic-20220509-r1 x86_64 {linux-firmware} (custom:multiple) [installed]
"""

# The fixture's ``fact`` field -- the documented list-of-versions shape.
PACKAGES_2_FACT = {
    "musl": ["1.1.22-r3"],
    "zlib": ["1.2.11-r1"],
    "apk-tools": ["2.10.4-r2"],
    "musl-utils": ["1.1.22-r3"],
    "iproute2": ["5.17.0-r0"],
    "linux-firmware-rtl_nic": ["20220509-r1"],
}


async def test_apk_packages_parses_the_warning_fixture():
    """Over the real-transport path, ``ApkPackages`` parses the fixture's
    rows into pyinfra's fixture fact: the ``WARNING:`` lines carry no
    ``{origin} (license)`` tail and never match the regex."""
    host = CannedShellHost(PACKAGES_2_OUTPUT)
    assert await ApkPackages.process(host) == PACKAGES_2_FACT
    assert host.last_shell == ApkPackages.COMMAND


# --- Real-transport parity (pyinfra's apk.ApkPackages/packages.json) ----------

# A selected set of the ``output`` field of pyinfra's
# ``apk.ApkPackages/packages.json`` fixture (the modern-alpine 143-row
# listing), verbatim rows covering the regex's edge cases: multi-dash names
# (``alpine-baselayout-data``, ``openssh-client-common``), names with
# punctuation (``libstdc++``, ``p11-kit``, ``json-c``), underscore- and
# plus-bearing versions (``13.2.1_git20240309-r0``), a colon-bearing
# ``{origin}`` split from the name by the last ``-r<release>`` boundary
# (``apk-tools``), and licenses whose dashes and multi-token spellings
# must not confuse the name/version boundary.
PACKAGES_OUTPUT = """\
alpine-baselayout-data-3.6.5-r0 x86_64 {alpine-baselayout} (GPL-2.0-only) [installed]
alpine-keys-2.4-r1 x86_64 {alpine-keys} (MIT) [installed]
apk-tools-2.14.4-r0 x86_64 {apk-tools} (GPL-2.0-only) [installed]
argon2-libs-20190702-r5 x86_64 {argon2} (Apache-2.0 OR CC0-1.0) [installed]
busybox-1.36.1-r29 x86_64 {busybox} (GPL-2.0-only) [installed]
json-c-0.17-r0 x86_64 {json-c} (MIT) [installed]
libgcc-13.2.1_git20240309-r0 x86_64 {gcc} (GPL-2.0-or-later AND LGPL-2.1-or-later) [installed]
libstdc++-13.2.1_git20240309-r0 x86_64 {gcc} (GPL-2.0-or-later AND LGPL-2.1-or-later) [installed]
musl-utils-1.2.5-r0 x86_64 {musl} (MIT AND BSD-2-Clause AND GPL-2.0-or-later) [installed]
openssh-client-common-9.7_p1-r4 x86_64 {openssh} (SSH-OpenSSH) [installed]
p11-kit-0.25.3-r0 x86_64 {p11-kit} (BSD-3-Clause) [installed]
syslinux-6.04_pre1-r15 x86_64 {syslinux} (GPL-2.0-or-later) [installed]
xz-libs-5.6.2-r0 x86_64 {xz} (GPL-2.0-or-later AND 0BSD AND Public-Domain AND LGPL-2.1-or-later) [installed]
"""

# The fixture fact restricted to the selected rows above -- every name
# split at the last ``-r<release>`` boundary before the ``{origin}`` tail.
ALPINE_PACKAGES = {
    "alpine-baselayout-data": ["3.6.5-r0"],
    "alpine-keys": ["2.4-r1"],
    "apk-tools": ["2.14.4-r0"],
    "argon2-libs": ["20190702-r5"],
    "busybox": ["1.36.1-r29"],
    "json-c": ["0.17-r0"],
    "libgcc": ["13.2.1_git20240309-r0"],
    "libstdc++": ["13.2.1_git20240309-r0"],
    "musl-utils": ["1.2.5-r0"],
    "openssh-client-common": ["9.7_p1-r4"],
    "p11-kit": ["0.25.3-r0"],
    "syslinux": ["6.04_pre1-r15"],
    "xz-libs": ["5.6.2-r0"],
}


async def test_apk_packages_parses_the_alpine_fixture():
    """The selected fixture rows parse into the fixture's fact subset: the
    greedy name group splits at the last ``-<version>-r<release>``
    boundary, keeping ``+``, ``:`` and ``_`` punctuation intact."""
    host = CannedShellHost(PACKAGES_OUTPUT)
    assert await ApkPackages.process(host) == ALPINE_PACKAGES


# --- Parser edge cases ---------------------------------------------------------


async def test_apk_packages_empty_reads_as_empty_dict():
    """No rows at all -- empty stdout or only blank lines and warnings --
    reads as {} (pyinfra's ``default = dict``)."""
    assert await ApkPackages.process(CannedShellHost("")) == {}
    assert await ApkPackages.process(CannedShellHost("\n\n")) == {}
    noise = "WARNING: Ignoring APKINDEX.00740ba1.tar.gz: No such file or directory\n"
    assert await ApkPackages.process(CannedShellHost(noise)) == {}


async def test_apk_packages_ignores_rows_without_the_origin_tail():
    """A line missing the ``{origin} (license)`` tail -- e.g. a truncated
    listing or plain text -- never matches, while well-formed rows next to
    it still parse."""
    stdout = """\
musl 1.2.5-r0 x86_64
musl-1.2.5-r0 x86_64 {musl} (MIT) [installed]
( 100% installed)
"""
    host = CannedShellHost(stdout)
    assert await ApkPackages.process(host) == {"musl": ["1.2.5-r0"]}


async def test_apk_packages_dedups_and_sorts_versions():
    """The same name listed twice contributes one sorted, de-duplicated
    version list -- pyinfra's parse_packages set semantics, returned as
    the documented sorted list."""
    stdout = """\
musl-1.2.5-r0 x86_64 {musl} (MIT) [installed]
musl-1.1.22-r3 x86_64 {musl} (MIT) [installed]
musl-1.2.5-r0 x86_64 {musl} (MIT) [installed]
"""
    host = CannedShellHost(stdout)
    assert await ApkPackages.process(host) == {"musl": ["1.1.22-r3", "1.2.5-r0"]}


# --- SimulatedHost seam -------------------------------------------------------


async def test_apk_packages_reads_the_simulated_seam():
    """A SimulatedHost exposes ``apk_packages`` directly, bypassing the
    shell; the default seam is {} -- no installed packages listed."""
    host = SimulatedHost(name="h", apk_packages=PACKAGES_2_FACT)
    assert await ApkPackages.process(host) == PACKAGES_2_FACT
    assert await ApkPackages.process(SimulatedHost(name="h")) == {}


async def test_apk_packages_seam_returns_copies():
    """Mutating the returned dict or a version list never touches the
    simulated state."""
    host = SimulatedHost(name="h", apk_packages=PACKAGES_2_FACT)
    packages = await ApkPackages.process(host)
    packages["musl"].append("9.9-r0")
    packages["stray"] = ["1.0-r0"]
    del packages["zlib"]
    assert await ApkPackages.process(host) == PACKAGES_2_FACT


# --- gather_fact fan-out ------------------------------------------------------


async def test_gather_fact_apk_packages_reads_simulated_seams():
    """Across a simulated fleet each host answers from its own seam state,
    keyed by host name in inventory order."""
    host1 = SimulatedHost(name="web1", apk_packages=PACKAGES_2_FACT)
    host2 = SimulatedHost(name="web2")
    packages = await gather_fact([host1, host2], ApkPackages)
    assert packages == {"web1": PACKAGES_2_FACT, "web2": {}}


async def test_gather_fact_apk_packages_over_canned_shells():
    """The fan-out drives every host's real-transport path with the same
    COMMAND and keys the parsed results by host name."""
    host1 = CannedShellHost(PACKAGES_2_OUTPUT)
    host1.name = "web1"
    host2 = CannedShellHost("")
    host2.name = "web2"
    packages = await gather_fact([host1, host2], ApkPackages)
    assert packages == {"web1": PACKAGES_2_FACT, "web2": {}}
    assert host1.last_shell == ApkPackages.COMMAND
    assert host2.last_shell == ApkPackages.COMMAND


# --- ApkExplicitPackages: command parity ---------------------------------------


def test_apk_explicit_packages_command_cats_the_world_file():
    """The command is a plain cat of ``/etc/apk/world`` behind a ``|| true``,
    so a missing file (any non-Alpine host) reads as ``[]`` instead of
    failing the gather -- the same shape as ``ApkRepositories``' command."""
    assert ApkExplicitPackages.PATH == "/etc/apk/world"
    assert (
        ApkExplicitPackages.COMMAND
        == f"cat -- {ApkExplicitPackages.PATH} 2>/dev/null || true"
    )


# --- ApkExplicitPackages: real-transport parity (original fact, canned output) --

# A realistic modern-Alpine ``/etc/apk/world``: the bare names apk writes for
# the base system, a version-pinned package, a package allowed from a tagged
# repository, a prefix-bound constraint and a conflict constraint. (pyinfra
# ships no apk world fact, hence no fixture.)
WORLD_OUTPUT = """\
# explicitly requested packages
alpine-baselayout
alpine-keys
apk-tools
busybox
ca-certificates
curl=8.14.0-r0
nginx@edge
openssh>~9.7_p1-r4
!legacy-package
"""

# What the file above selects: the eight package names, in sorted order, with
# the tag, the version bounds and the conflict line all dropped.
WORLD_FACT = [
    "alpine-baselayout",
    "alpine-keys",
    "apk-tools",
    "busybox",
    "ca-certificates",
    "curl",
    "nginx",
    "openssh",
]


async def test_apk_explicit_packages_parses_the_canned_file():
    """Over the real-transport path, ``ApkExplicitPackages`` parses the
    canned world file into the sorted name list: every constraint character
    ends the name, the comment line never contributes, and the ``!``
    conflict constraint names a package that must NOT be installed."""
    host = CannedShellHost(WORLD_OUTPUT)
    assert await ApkExplicitPackages.process(host) == WORLD_FACT
    assert host.last_shell == ApkExplicitPackages.COMMAND


async def test_apk_explicit_packages_result_is_json_printable():
    """The plain list of names serializes directly, like the sibling
    name-list facts -- the example prints the fact with ``json.dumps``."""
    parsed = json.dumps(WORLD_FACT, indent=2)
    assert '"nginx"' in parsed


async def test_apk_explicit_packages_empty_or_missing_reads_as_empty_list():
    """Empty stdout -- what the ``|| true`` command leaves behind for a
    missing or empty file -- reads as ``[]``, and so does a file holding
    only a comment and blank lines."""
    assert await ApkExplicitPackages.process(CannedShellHost("")) == []
    only_noise = "# nothing requested\n\n"
    assert await ApkExplicitPackages.process(CannedShellHost(only_noise)) == []


async def test_apk_explicit_packages_skips_lines_without_a_name():
    """A line whose constraint carries no package name -- a lone ``@tag``,
    a bare version bound -- is not a world constraint and is skipped, while
    well-formed neighbors still parse."""
    stdout = """\
@edge
=1.2.3-r0
>=
busybox
musl=1.2.5-r0
"""
    host = CannedShellHost(stdout)
    assert await ApkExplicitPackages.process(host) == ["busybox", "musl"]


async def test_apk_explicit_packages_dedups_and_sorts_names():
    """The same name pinned and tagged in separate lines collapses into one
    entry of the sorted list -- the constraint never splits the name."""
    stdout = """\
nginx@edge
musl=1.2.5-r0
musl
nginx
"""
    host = CannedShellHost(stdout)
    assert await ApkExplicitPackages.process(host) == ["musl", "nginx"]


# --- ApkExplicitPackages: SimulatedHost seam -----------------------------------


async def test_apk_explicit_packages_reads_the_simulated_seam():
    """A SimulatedHost exposes ``apk_explicit_packages`` directly, bypassing
    the shell; the default seam is ``[]`` -- nothing explicitly requested."""
    host = SimulatedHost(name="h", apk_explicit_packages=WORLD_FACT)
    assert await ApkExplicitPackages.process(host) == WORLD_FACT
    assert await ApkExplicitPackages.process(SimulatedHost(name="h")) == []


async def test_apk_explicit_packages_seam_returns_copies():
    """Mutating the returned list never touches the simulated state."""
    host = SimulatedHost(name="h", apk_explicit_packages=WORLD_FACT)
    names = await ApkExplicitPackages.process(host)
    names.append("stray")
    del names[0]
    assert await ApkExplicitPackages.process(host) == WORLD_FACT


# --- ApkExplicitPackages: gather_fact fan-out ----------------------------------


async def test_gather_fact_apk_explicit_packages_reads_simulated_seams():
    """Across a simulated fleet each host answers from its own seam state,
    keyed by host name in inventory order."""
    host1 = SimulatedHost(name="web1", apk_explicit_packages=WORLD_FACT)
    host2 = SimulatedHost(name="web2")
    names = await gather_fact([host1, host2], ApkExplicitPackages)
    assert names == {"web1": WORLD_FACT, "web2": []}


async def test_gather_fact_apk_explicit_packages_over_canned_shells():
    """The fan-out drives every host's real-transport path with the same
    COMMAND and keys the parsed results by host name."""
    host1 = CannedShellHost(WORLD_OUTPUT)
    host1.name = "web1"
    host2 = CannedShellHost("")
    host2.name = "web2"
    names = await gather_fact([host1, host2], ApkExplicitPackages)
    assert names == {"web1": WORLD_FACT, "web2": []}
    assert host1.last_shell == ApkExplicitPackages.COMMAND
    assert host2.last_shell == ApkExplicitPackages.COMMAND


# --- ApkHeldPackages: command parity -----------------------------------------


def test_apk_held_packages_command_cats_the_world_file():
    """The command is the same plain cat of ``/etc/apk/world`` behind a
    ``|| true`` that ``ApkExplicitPackages`` builds -- the two facts read the
    one file -- so a missing file (any non-Alpine host) reads as ``{}``."""
    assert ApkHeldPackages.PATH == "/etc/apk/world"
    assert (
        ApkHeldPackages.COMMAND
        == f"cat -- {ApkHeldPackages.PATH} 2>/dev/null || true"
    )
    assert ApkHeldPackages.COMMAND == ApkExplicitPackages.COMMAND


# --- ApkHeldPackages: real-transport parity (original fact, canned output) ----

# What the WORLD_OUTPUT file above holds: only the two lines carrying a
# version bound. The bare base-system names, the ``nginx@edge`` tag-only
# selection (a repository choice, not a hold) and the ``!legacy-package``
# conflict line all drop out; each kept line maps its bare name to the
# constraint from the operator on.
HELD_FACT = {"curl": "=8.14.0-r0", "openssh": ">~9.7_p1-r4"}


async def test_apk_held_packages_parses_the_canned_file():
    """Over the real-transport path, ``ApkHeldPackages`` parses the canned
    world file into name -> constraint: only the version-bound lines
    contribute, the tag-only ``nginx@edge`` and the bare names drop out."""
    host = CannedShellHost(WORLD_OUTPUT)
    assert await ApkHeldPackages.process(host) == HELD_FACT
    assert host.last_shell == ApkHeldPackages.COMMAND


async def test_apk_held_packages_result_is_json_printable():
    """The plain dict of name -> constraint serializes directly -- the
    example prints the fact with ``json.dumps``."""
    parsed = json.dumps(HELD_FACT, indent=2)
    assert '"curl": "=8.14.0-r0"' in parsed


async def test_apk_held_packages_empty_or_missing_reads_as_empty_dict():
    """Empty stdout -- what the ``|| true`` command leaves behind for a
    missing or empty file -- reads as ``{}``, and so does a file holding
    only comments, blank lines and unpinned names."""
    assert await ApkHeldPackages.process(CannedShellHost("")) == {}
    only_unpinned = "# nothing pinned\n\nbusybox\nalpine-keys\n"
    assert await ApkHeldPackages.process(CannedShellHost(only_unpinned)) == {}


async def test_apk_held_packages_tag_only_line_is_not_a_hold():
    """A ``@tag`` selects a package from a tagged repository; with no version
    bound it is not a hold, so ``nginx@edge`` contributes nothing while a
    pinned neighbour does."""
    stdout = """\
nginx@edge
curl=8.14.0-r0
"""
    host = CannedShellHost(stdout)
    assert await ApkHeldPackages.process(host) == {"curl": "=8.14.0-r0"}


async def test_apk_held_packages_keeps_every_operator_form():
    """Each apk version operator (``=``, ``<``, ``~`` and the doubled ``>~``)
    is kept verbatim in the constraint, and the bare name is split at the
    first boundary character so a ``@tag`` never lands in the key."""
    stdout = """\
a=1.0-r0
b<2.0-r0
c>~3.0-r0
d~4.0
e@edge=5.0-r0
"""
    host = CannedShellHost(stdout)
    assert await ApkHeldPackages.process(host) == {
        "a": "=1.0-r0",
        "b": "<2.0-r0",
        "c": ">~3.0-r0",
        "d": "~4.0",
        "e": "=5.0-r0",
    }


async def test_apk_held_packages_skips_lines_without_a_name():
    """A line whose constraint carries no package name -- a lone operator or
    tag -- is not a world constraint and is skipped, while well-formed
    neighbours still parse."""
    stdout = """\
@edge
=1.2.3-r0
>=
busybox
musl=1.2.5-r0
"""
    host = CannedShellHost(stdout)
    assert await ApkHeldPackages.process(host) == {"musl": "=1.2.5-r0"}


async def test_apk_held_packages_last_line_wins_for_a_repeated_name():
    """A name pinned on two world lines keeps the last constraint -- entries
    follow file order, so a later assignment overwrites the earlier."""
    stdout = """\
musl=1.2.5-r0
musl=1.2.6-r0
"""
    host = CannedShellHost(stdout)
    assert await ApkHeldPackages.process(host) == {"musl": "=1.2.6-r0"}


# --- ApkHeldPackages: SimulatedHost seam -------------------------------------


async def test_apk_held_packages_reads_the_simulated_seam():
    """A SimulatedHost exposes ``apk_held_packages`` directly, bypassing the
    shell; the default seam is ``{}`` -- nothing pinned."""
    host = SimulatedHost(name="h", apk_held_packages=HELD_FACT)
    assert await ApkHeldPackages.process(host) == HELD_FACT
    assert await ApkHeldPackages.process(SimulatedHost(name="h")) == {}


async def test_apk_held_packages_seam_returns_copies():
    """Mutating the returned dict never touches the simulated state."""
    host = SimulatedHost(name="h", apk_held_packages=HELD_FACT)
    held = await ApkHeldPackages.process(host)
    held["stray"] = "=1.0"
    del held["curl"]
    assert await ApkHeldPackages.process(host) == HELD_FACT


# --- ApkHeldPackages: gather_fact fan-out ------------------------------------


async def test_gather_fact_apk_held_packages_reads_simulated_seams():
    """Across a simulated fleet each host answers from its own seam state,
    keyed by host name in inventory order."""
    host1 = SimulatedHost(name="web1", apk_held_packages=HELD_FACT)
    host2 = SimulatedHost(name="web2")
    held = await gather_fact([host1, host2], ApkHeldPackages)
    assert held == {"web1": HELD_FACT, "web2": {}}


async def test_gather_fact_apk_held_packages_over_canned_shells():
    """The fan-out drives every host's real-transport path with the same
    COMMAND and keys the parsed results by host name."""
    host1 = CannedShellHost(WORLD_OUTPUT)
    host1.name = "web1"
    host2 = CannedShellHost("")
    host2.name = "web2"
    held = await gather_fact([host1, host2], ApkHeldPackages)
    assert held == {"web1": HELD_FACT, "web2": {}}
    assert host1.last_shell == ApkHeldPackages.COMMAND
    assert host2.last_shell == ApkHeldPackages.COMMAND


# --- ApkRepositories: command parity ------------------------------------------


def test_apk_repositories_command_cats_the_repositories_file():
    """The command is a plain cat of ``/etc/apk/repositories`` behind a
    ``|| true``, so a missing file (any non-Alpine host) reads as ``[]`
    instead of failing the gather."""
    assert ApkRepositories.PATH == "/etc/apk/repositories"
    assert ApkRepositories.COMMAND == f"cat -- {ApkRepositories.PATH} 2>/dev/null || true"


# --- ApkRepositories: real-transport parity (original fact, canned output) ----

# A realistic modern-Alpine ``/etc/apk/repositories``: a leading comment
# block, the two untagged v3.19 lines, blank lines, and a tagged edge
# repository. (pyinfra ships no apk repositories fact, hence no fixture.)
REPOSITORIES_OUTPUT = """\
# Alpine package repositories
https://dl-cdn.alpinelinux.org/alpine/v3.19/main
https://dl-cdn.alpinelinux.org/alpine/v3.19/community

# additional testing repository
@testing https://dl-cdn.alpinelinux.org/alpine/edge/testing
"""

# What the file above configures: four repositories in file order, the
# tagged one carrying its tag without the leading ``@``.
REPOSITORIES_FACT = [
    {"url": "https://dl-cdn.alpinelinux.org/alpine/v3.19/main", "tag": ""},
    {"url": "https://dl-cdn.alpinelinux.org/alpine/v3.19/community", "tag": ""},
    {"url": "https://dl-cdn.alpinelinux.org/alpine/edge/testing", "tag": "testing"},
]


async def test_apk_repositories_parses_the_canned_file():
    """Over the real-transport path, ``ApkRepositories`` parses the canned
    file into ``{url, tag}`` dicts in file order; comments and blank lines
    never contribute entries."""
    host = CannedShellHost(REPOSITORIES_OUTPUT)
    assert await ApkRepositories.process(host) == REPOSITORIES_FACT
    assert host.last_shell == ApkRepositories.COMMAND


async def test_apk_repositories_result_is_json_printable():
    """The plain-dict shape serializes directly, like ``AptSources``'
    entries -- the examples print the fact with ``json.dumps``."""
    parsed = json.dumps(REPOSITORIES_FACT, indent=2)
    assert '"tag": "testing"' in parsed


# --- ApkRepositories: parser edge cases ----------------------------------------


async def test_apk_repositories_empty_or_missing_reads_as_empty_list():
    """Empty stdout -- what the ``|| true`` command leaves behind for a
    missing or empty file -- reads as ``[]``, and so does a file holding
    only comments and blank lines."""
    assert await ApkRepositories.process(CannedShellHost("")) == []
    only_noise = "# only a comment\n\n"
    assert await ApkRepositories.process(CannedShellHost(only_noise)) == []


async def test_apk_repositories_skips_malformed_lines():
    """A line that is not exactly an optional tag plus one URL -- a lone
    ``@tag``, several URLs on one line -- is not a repository line and is
    skipped, while well-formed neighbors still parse."""
    stdout = """\
@onlytag
https://a.example.com/v1 https://b.example.com/v2
https://dl-cdn.alpinelinux.org/alpine/v3.19/main
  @edge  https://dl-cdn.alpinelinux.org/alpine/edge/community
"""
    host = CannedShellHost(stdout)
    assert await ApkRepositories.process(host) == [
        {"url": "https://dl-cdn.alpinelinux.org/alpine/v3.19/main", "tag": ""},
        {"url": "https://dl-cdn.alpinelinux.org/alpine/edge/community", "tag": "edge"},
    ]


# --- ApkRepositories: SimulatedHost seam ---------------------------------------


async def test_apk_repositories_reads_the_simulated_seam():
    """A SimulatedHost exposes ``apk_repositories`` directly, bypassing the
    shell; the default seam is ``[]`` -- no repositories configured."""
    host = SimulatedHost(name="h", apk_repositories=REPOSITORIES_FACT)
    assert await ApkRepositories.process(host) == REPOSITORIES_FACT
    assert await ApkRepositories.process(SimulatedHost(name="h")) == []


async def test_apk_repositories_seam_returns_copies():
    """Mutating a returned dict, or the returned list, never touches the
    simulated state."""
    host = SimulatedHost(name="h", apk_repositories=REPOSITORIES_FACT)
    repositories = await ApkRepositories.process(host)
    repositories[0]["tag"] = "mutated"
    repositories.append({"url": "https://stray.example.com", "tag": ""})
    assert await ApkRepositories.process(host) == REPOSITORIES_FACT


# --- ApkRepositories: gather_fact fan-out --------------------------------------


async def test_gather_fact_apk_repositories_reads_simulated_seams():
    """Across a simulated fleet each host answers from its own seam state,
    keyed by host name in inventory order."""
    host1 = SimulatedHost(name="web1", apk_repositories=REPOSITORIES_FACT)
    host2 = SimulatedHost(name="web2")
    repositories = await gather_fact([host1, host2], ApkRepositories)
    assert repositories == {"web1": REPOSITORIES_FACT, "web2": []}


async def test_gather_fact_apk_repositories_over_canned_shells():
    """The fan-out drives every host's real-transport path with the same
    COMMAND and keys the parsed results by host name."""
    host1 = CannedShellHost(REPOSITORIES_OUTPUT)
    host1.name = "web1"
    host2 = CannedShellHost("")
    host2.name = "web2"
    repositories = await gather_fact([host1, host2], ApkRepositories)
    assert repositories == {"web1": REPOSITORIES_FACT, "web2": []}
    assert host1.last_shell == ApkRepositories.COMMAND
    assert host2.last_shell == ApkRepositories.COMMAND


# --- ApkPackage: command and shell quoting ------------------------------------


def test_apk_package_command_queries_the_package_and_quotes_it():
    """``apk info -a`` prints every field apk knows about the package, with
    the ``2>/dev/null || true`` tail so an absent package (or a host without
    apk) exits non-zero yet leaves stdout empty, letting ``process`` report
    ``None``. The name is ``shlex``-quoted -- a name carrying a shell
    metacharacter cannot break out of the argument."""
    assert ApkPackage.command("curl") == "apk info -a curl 2>/dev/null || true"
    assert ApkPackage.command("a;b") == "apk info -a 'a;b' 2>/dev/null || true"


# --- ApkPackage: real-transport parity (original fact, canned output) ---------

# A realistic modern-Alpine ``apk info -a curl`` block. apk prints every
# field as a ``name-version Label:`` header followed by its value lines; the
# labels are capitalized (``Description``, ``Installed size``, ``Webpage``,
# ``Depends on``, ``Provides``) except ``is required by``. The ``Contains``
# file listing and ``is required by`` reverse-dependency sections are present
# precisely to prove they never leak into the tracked ``provides`` list. (pyinfra
# ships no per-package apk info fact, hence no fixture.)
INFO_OUTPUT = """\
curl-8.14.0-r0 Description:
URL retrieval utility and library
curl-8.14.0-r0 Webpage:
https://curl.se/
curl-8.14.0-r0 License:
curl
curl-8.14.0-r0 Installed size:
1085898
curl-8.14.0-r0 Depends on:
so:libc.musl-x86_64.so.1
so:libz.so.1
curl-8.14.0-r0 Provides:
curl-doc
curl-8.14.0-r0 Contains:
usr/bin/curl
usr/bin/curl-config
usr/lib/libcurl.so.4.8.0
curl-8.14.0-r0 is required by:
wget-1.21.4-r2
"""

# What the block above selects: the six tracked scalars and lists, with the
# untracked ``Contains``/``is required by`` sections dropped.
CURL_FACT = {
    "name": "curl",
    "version": "8.14.0-r0",
    "description": "URL retrieval utility and library",
    "url": "https://curl.se/",
    "license": "curl",
    "size": 1085898,
    "depends": ["so:libc.musl-x86_64.so.1", "so:libz.so.1"],
    "provides": ["curl-doc"],
}


async def test_apk_package_parses_the_canned_info():
    """Over the real-transport path, ``ApkPackage`` turns the canned block
    into the documented ``ApkPackageInfo``: capitalized labels lower-case onto
    their keys, ``Webpage`` becomes ``url``, ``Installed size`` becomes the
    ``size`` int, and the untracked ``Contains``/``is required by`` sections
    collect nothing."""
    host = CannedShellHost(INFO_OUTPUT)
    assert await ApkPackage.process(host, "curl") == CURL_FACT
    assert host.last_shell == ApkPackage.command("curl")


async def test_apk_package_result_is_json_printable():
    """The plain-dict shape serializes directly, like ``AptPolicy``'s entry --
    the example prints the fact with ``json.dumps``."""
    parsed = json.dumps(CURL_FACT, indent=2)
    assert '"so:libz.so.1"' in parsed


# --- ApkPackage: parser edge cases --------------------------------------------


async def test_apk_package_absent_or_noise_reads_as_none():
    """Empty stdout -- what the ``|| true`` command leaves behind for an
    absent package or a host without apk -- reads as ``None``, and so does
    output with no field header (apk's index-warning noise alone). This is
    ``apt.DebPackage``/``dnf.RpmPackage``'s None-on-absent, not pyinfra's
    empty-dict."""
    assert await ApkPackage.process(CannedShellHost(""), "nope") is None
    noise = "WARNING: Ignoring APKINDEX.00740ba1.tar.gz: No such file or directory\n"
    assert await ApkPackage.process(CannedShellHost(noise), "nope") is None


async def test_apk_package_absent_fields_read_as_defaults():
    """A package whose only tracked section is the description leaves the
    other scalars at their empty defaults (``""``/``0``) and the two list
    fields empty."""
    stdout = "busybox-1.36.1-r29 Description:\nSize optimized toolbox\n"
    assert await ApkPackage.process(CannedShellHost(stdout), "busybox") == {
        "name": "busybox",
        "version": "1.36.1-r29",
        "description": "Size optimized toolbox",
        "url": "",
        "license": "",
        "size": 0,
        "depends": [],
        "provides": [],
    }


async def test_apk_package_non_numeric_size_reads_as_zero():
    """A malformed ``Installed size`` value coerces to ``0`` rather than
    raising, so a stray line never breaks the gather."""
    stdout = "musl-1.2.5-r0 Description:\nlibc\nmusl-1.2.5-r0 Installed size:\nnot-a-number\n"
    info = await ApkPackage.process(CannedShellHost(stdout), "musl")
    assert info["size"] == 0


async def test_apk_package_splits_multi_dash_names_from_versions():
    """The header token splits at the last ``-r<release>`` boundary, exactly
    as ``ApkPackages`` splits its listing rows, so a multi-dash name keeps
    every dash before its version."""
    stdout = "alpine-baselayout-data-3.6.5-r0 Description:\nAlpine base dir structure\n"
    info = await ApkPackage.process(CannedShellHost(stdout), "alpine-baselayout-data")
    assert (info["name"], info["version"]) == ("alpine-baselayout-data", "3.6.5-r0")


# --- ApkPackage: SimulatedHost seam -------------------------------------------


async def test_apk_package_reads_the_simulated_seam():
    """A SimulatedHost answers from its package-keyed ``apk_package_info``
    store, bypassing the shell; a name absent from it reads as ``None`` -- an
    absent package -- and so does the default empty store."""
    host = SimulatedHost(name="h", apk_package_info={"curl": CURL_FACT})
    assert await ApkPackage.process(host, "curl") == CURL_FACT
    assert await ApkPackage.process(host, "nginx") is None
    assert await ApkPackage.process(SimulatedHost(name="h"), "curl") is None


async def test_apk_package_seam_returns_copies():
    """Mutating a returned list field never touches the simulated state, so
    the seam hands back a fresh dict and fresh ``depends``/``provides`` lists."""
    host = SimulatedHost(name="h", apk_package_info={"curl": CURL_FACT})
    info = await ApkPackage.process(host, "curl")
    info["depends"].append("so:stray.so.1")
    info["provides"].clear()
    info["size"] = -1
    assert await ApkPackage.process(host, "curl") == CURL_FACT


# --- ApkPackage: gather_fact fan-out ------------------------------------------


async def test_gather_fact_apk_package_reads_simulated_seams():
    """Across a simulated fleet the package argument reaches every host's
    seam; each answers from its own store, keyed by host name."""
    host1 = SimulatedHost(name="web1", apk_package_info={"curl": CURL_FACT})
    host2 = SimulatedHost(name="web2")
    infos = await gather_fact([host1, host2], ApkPackage, "curl")
    assert infos == {"web1": CURL_FACT, "web2": None}


async def test_gather_fact_apk_package_over_canned_shells():
    """The fan-out drives every host's real-transport path with the quoted
    per-package command and keys the parsed results by host name."""
    host1 = CannedShellHost(INFO_OUTPUT)
    host1.name = "web1"
    host2 = CannedShellHost("")
    host2.name = "web2"
    infos = await gather_fact([host1, host2], ApkPackage, "curl")
    assert infos == {"web1": CURL_FACT, "web2": None}
    assert host1.last_shell == ApkPackage.command("curl")
    assert host2.last_shell == ApkPackage.command("curl")


# --- apk.packages: command descriptors ---------------------------------------


def test_apk_install_command_renders_apk_add():
    """``apk add`` needs no ``-y`` (apk never prompts): pyinfra's bare
    install command plus the shell-quoted names."""
    assert ApkInstallCommand(packages=("nginx", "vim")).shell() == "apk add nginx vim"


def test_apk_remove_command_renders_apk_del():
    assert ApkRemoveCommand(packages=("nginx",)).shell() == "apk del nginx"


def test_apk_upgrade_command_renders_apk_upgrade():
    assert ApkUpgradeCommand(packages=("nginx",)).shell() == "apk upgrade nginx"


def test_apk_package_commands_are_elevated_by_sudo():
    """Installing/removing/upgrading needs root, so the request's ``sudo=True``
    prefixes the rendered shell (the run-as user comes from the inventory
    ``privilege``, never the operation)."""
    cmd = ApkInstallCommand(packages=("nginx",))
    assert command_shell(cmd) == "apk add nginx"
    assert command_shell(cmd, sudo=True) == "sudo apk add nginx"


def test_apk_package_commands_shell_quote_the_names():
    """Package names are shell-quoted like apt's and dnf's, so a metacharacter
    in a name can never reach the remote shell unescaped."""
    package = "nginx;touch /tmp/pwned"
    quoted = shlex.quote(package)
    assert quoted != package
    assert ApkInstallCommand(packages=(package,)).shell() == f"apk add {quoted}"
    assert ApkRemoveCommand(packages=(package,)).shell() == f"apk del {quoted}"
    assert ApkUpgradeCommand(packages=(package,)).shell() == f"apk upgrade {quoted}"


# --- apk.packages: idempotence (pyinfra parity) ------------------------------


async def test_apk_packages_installs_missing_packages():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apk.packages(ctx, packages=("nginx", "vim"))]
    assert ctx.changed
    assert ctx.reason == "2 package(s) to install"
    assert commands == [ApkInstallCommand(packages=("nginx", "vim"))]


async def test_apk_packages_no_change_when_present():
    host = SimulatedHost(name="h", apk_packages={"nginx": ["1.23.0-r0"]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apk.packages(ctx, packages=("nginx",))]
    assert not ctx.changed
    assert ctx.reason == "all packages present"  # recorded even when unchanged
    assert commands == []


async def test_apk_packages_accepts_a_bare_string():
    """pyinfra accepts ``packages="nginx"`` as shorthand for a one-item list."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apk.packages(ctx, packages="nginx")]
    assert commands == [ApkInstallCommand(packages=("nginx",))]


async def test_apk_packages_none_is_a_noop():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apk.packages(ctx)]
    assert not ctx.changed
    assert ctx.reason == "no packages requested"
    assert commands == []


async def test_apk_packages_removes_installed_packages():
    host = SimulatedHost(name="h", apk_packages={"nginx": ["1.23.0-r0"]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apk.packages(ctx, packages=("nginx",), present=False)]
    assert ctx.changed
    assert ctx.reason == "1 package(s) to remove"
    assert commands == [ApkRemoveCommand(packages=("nginx",))]


async def test_apk_packages_remove_noop_when_absent():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apk.packages(ctx, packages=("nginx",), present=False)]
    assert not ctx.changed
    assert ctx.reason == "no packages to remove"
    assert commands == []


# --- apk.packages: version pins ----------------------------------------------


async def test_apk_packages_version_pin_present_only_on_exact_version():
    """``ApkPackages`` reports versions, so a pinned ``<pkg>=<version>`` is
    present only when that exact version is installed (apt's rule, not dnf's
    name-only match)."""
    host = SimulatedHost(name="h", apk_packages={"nginx": ["1.23.0-r0"]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apk.packages(ctx, packages=("nginx=1.23.0-r0",))]
    assert not ctx.changed
    assert ctx.reason == "all packages present"
    assert commands == []


async def test_apk_packages_version_pin_installs_when_version_differs():
    host = SimulatedHost(name="h", apk_packages={"nginx": ["1.23.0-r0"]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apk.packages(ctx, packages=("nginx=1.24.0-r0",))]
    assert ctx.changed
    assert commands == [ApkInstallCommand(packages=("nginx=1.24.0-r0",))]
    assert commands[0].shell() == "apk add nginx=1.24.0-r0"


async def test_apk_packages_version_pin_splits_on_the_last_equals():
    """The pin splits on the *last* ``=`` (pyinfra's ``rsplit`` join, apt's and
    dnf's ``rpartition``), so an ``=`` inside the name never cuts it short:
    the installed name ``foo=1.0`` matches the pin ``foo=1.0=2``."""
    host = SimulatedHost(name="h", apk_packages={"foo=1.0": ["2"]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apk.packages(ctx, packages=("foo=1.0=2",))]
    assert not ctx.changed
    assert ctx.reason == "all packages present"
    assert commands == []


# --- apk.packages: latest ----------------------------------------------------


async def test_apk_packages_latest_upgrades_installed_unpinned_packages():
    """``latest`` upgrades present, unpinned packages with ``apk upgrade``;
    missing ones still install (the install command comes first)."""
    host = SimulatedHost(name="h", apk_packages={"nginx": ["1.23.0-r0"]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in apk.packages(ctx, packages=("nginx", "vim"), latest=True)
    ]
    assert commands == [
        ApkInstallCommand(packages=("vim",)),
        ApkUpgradeCommand(packages=("nginx",)),
    ]
    assert commands[1].shell() == "apk upgrade nginx"
    assert ctx.changed
    assert ctx.reason == "1 package(s) to install; 1 package(s) to upgrade"


async def test_apk_packages_latest_skips_version_pins():
    """A pinned ``<pkg>=<version>`` is never an upgrade candidate."""
    host = SimulatedHost(name="h", apk_packages={"nginx": ["1.23.0-r0"]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in apk.packages(ctx, packages=("nginx=1.23.0-r0",), latest=True)
    ]
    assert not ctx.changed
    assert ctx.reason == "all packages present"
    assert commands == []


# --- apk.packages: dropped options and kwargs --------------------------------


async def test_apk_packages_drops_update_and_upgrade_options():
    """pyinfra's ``update``/``upgrade`` prefix options are deliberately dropped:
    both keywords are absorbed by ``**kwargs`` with no effect, so only the
    install command is yielded -- no bare ``apk update``/``apk upgrade``
    prefix runs."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in apk.packages(ctx, packages=("nginx",), update=True, upgrade=True)
    ]
    assert commands == [ApkInstallCommand(packages=("nginx",))]
    assert ctx.reason == "1 package(s) to install"


async def test_apk_packages_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors pyinfra's global-arguments slot; op() already
    strips the ``name``/``sudo`` meta-parameters, the rest is ignored."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in apk.packages(ctx, packages=("nginx",), future_flag=True)
    ]
    assert commands == [ApkInstallCommand(packages=("nginx",))]


# --- apk.packages: SimulatedHost seam dispatch -------------------------------


async def test_apk_install_seam_mutates_state():
    """``send_command`` maps ``ApkInstallCommand`` onto ``apk_packages``: a pin
    records its exact version, an unpinned name the ``[""]`` placeholder."""
    host = SimulatedHost(name="h")
    await send_command(host, ApkInstallCommand(packages=("nginx",)))
    assert host.apk_packages == {"nginx": [""]}
    await send_command(host, ApkInstallCommand(packages=("vim=1.2-r3",)))
    assert host.apk_packages == {"nginx": [""], "vim": ["1.2-r3"]}


async def test_apk_remove_seam_mutates_state():
    host = SimulatedHost(name="h", apk_packages={"nginx": ["1.23.0-r0"]})
    await send_command(host, ApkRemoveCommand(packages=("nginx",)))
    assert host.apk_packages == {}


async def test_apk_upgrade_seam_leaves_versions():
    """The ``latest``-upgrade path has no newer-version metadata to apply, so
    the simulated ``apk upgrade`` leaves the installed versions untouched."""
    host = SimulatedHost(name="h", apk_packages={"nginx": ["1.23.0-r0"]})
    await send_command(host, ApkUpgradeCommand(packages=("nginx",)))
    assert host.apk_packages == {"nginx": ["1.23.0-r0"]}


# --- apk.packages: end to end ------------------------------------------------


async def test_apk_packages_end_to_end_install_then_noop():
    """Through run_prepare/run_apply: the install executes under sudo and
    renders in the log; a second prepare sees the name installed and plans no
    change (the fresh per-step fact cache observes the mutation)."""

    def install_deploy(host):
        yield op(apk.packages, name="Install nginx", packages=("nginx",), sudo=True)

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], install_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (ApkInstallCommand(packages=("nginx",)),)
    assert "$ sudo apk add nginx" in format_log(report)
    assert host.apk_packages == {"nginx": [""]}
    # A second prepare sees the name installed: no change.
    _runtimes, plans2 = await run_prepare([host], install_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "all packages present"


async def test_apk_packages_version_pin_install_simulates_by_exact_version():
    """A pinned install records the exact version, so a repeat pinned install
    is a no-op through the fresh per-step fact cache."""

    def pinned_deploy(host):
        yield op(
            apk.packages, name="Install pinned nginx", packages=("nginx=1.23.0-r0",)
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], pinned_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    assert report.log[0].changed
    assert host.apk_packages == {"nginx": ["1.23.0-r0"]}
    _runtimes, plans2 = await run_prepare([host], pinned_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "all packages present"


async def test_apk_packages_dry_run_plans_without_sending():
    """``dry=True`` stops after prepare: the install is planned as a change but
    there is no apply report, so nothing runs on the host."""

    def install_deploy(host):
        yield op(apk.packages, name="Install nginx", packages=("nginx",), sudo=True)

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
    assert planned.commands == (ApkInstallCommand(packages=("nginx",)),)
    # Nothing ran: the dry run sent no command.
    assert host.apk_packages == {}


# --- apk.virtual: command descriptor -----------------------------------------


def test_apk_virtual_command_renders_add_t():
    """The create direction renders apk's ``-t``/``--virtual`` add: the tag
    names the group and the packages follow as its dependencies."""
    cmd = ApkVirtualCommand(tag="build-deps", packages=("gcc", "musl-dev"))
    assert cmd.shell() == "apk add -t build-deps gcc musl-dev"


def test_apk_virtual_command_renders_del_for_removal():
    """The remove direction names only the group: ``apk del <tag>`` drops the
    virtual package and the members nothing else needs, so the descriptor's
    ``packages`` are unused here."""
    cmd = ApkVirtualCommand(tag="build-deps", packages=("gcc",), present=False)
    assert cmd.shell() == "apk del build-deps"


def test_apk_virtual_command_is_elevated_by_sudo():
    """Creating/removing a virtual group writes the root-owned apk database and
    world file, so the request's ``sudo=True`` prefixes the rendered shell (the
    run-as user comes from the inventory ``privilege``, never the operation)."""
    cmd = ApkVirtualCommand(tag="build-deps", packages=("gcc",))
    assert command_shell(cmd) == "apk add -t build-deps gcc"
    assert command_shell(cmd, sudo=True) == "sudo apk add -t build-deps gcc"
    removal = ApkVirtualCommand(tag="build-deps", present=False)
    assert command_shell(removal, su=True) == f"su -c {shlex.quote('apk del build-deps')}"


def test_apk_virtual_command_shell_quotes_tag_and_names():
    """The tag and the member names are shell-quoted, so a metacharacter in
    either can never reach the remote shell unescaped."""
    tag = "build deps;touch /tmp/pwned"
    package = "gcc`reboot`"
    cmd = ApkVirtualCommand(tag=tag, packages=(package,))
    assert cmd.shell() == f"apk add -t {shlex.quote(tag)} {shlex.quote(package)}"
    assert shlex.quote(tag) != tag


# --- apk.virtual: idempotence (world-name presence) --------------------------


async def test_apk_virtual_creates_absent_group():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in apk.virtual(ctx, tag="build-deps", packages=("gcc", "musl-dev"))
    ]
    assert ctx.changed
    assert ctx.reason == "virtual package build-deps will be created with 2 package(s)"
    assert commands == [
        ApkVirtualCommand(tag="build-deps", packages=("gcc", "musl-dev"), present=True)
    ]


async def test_apk_virtual_no_change_when_group_present():
    """The tag already in the world reads as present, so nothing is yielded --
    the reason is recorded even when unchanged (the plan shows it)."""
    host = SimulatedHost(name="h", apk_explicit_packages=["build-deps", "curl"])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in apk.virtual(ctx, tag="build-deps", packages=("gcc",))
    ]
    assert not ctx.changed
    assert ctx.reason == "virtual package build-deps already present"
    assert commands == []


async def test_apk_virtual_accepts_a_bare_string():
    """``packages="gcc"`` is shorthand for a one-item group, mirroring
    ``apk.packages``."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apk.virtual(ctx, tag="build-deps", packages="gcc")]
    assert commands == [
        ApkVirtualCommand(tag="build-deps", packages=("gcc",), present=True)
    ]


async def test_apk_virtual_present_without_packages_is_a_noop():
    """Creating a group with no members is a misuse (``apk add -t <tag>`` with
    nothing to add), so present=True with no packages yields nothing."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apk.virtual(ctx, tag="build-deps")]
    assert not ctx.changed
    assert ctx.reason == "no packages requested for virtual package build-deps"
    assert commands == []


async def test_apk_virtual_removes_present_group():
    host = SimulatedHost(name="h", apk_explicit_packages=["build-deps"])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in apk.virtual(ctx, tag="build-deps", present=False)
    ]
    assert ctx.changed
    assert ctx.reason == "virtual package build-deps will be removed"
    assert commands == [
        ApkVirtualCommand(tag="build-deps", packages=(), present=False)
    ]


async def test_apk_virtual_remove_noop_when_absent():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in apk.virtual(ctx, tag="build-deps", present=False)
    ]
    assert not ctx.changed
    assert ctx.reason == "virtual package build-deps not present"
    assert commands == []


async def test_apk_virtual_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors pyinfra's global-arguments slot; op() already
    strips the ``name``/``sudo``/``su`` meta-parameters, the rest is ignored."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in apk.virtual(
            ctx, tag="build-deps", packages=("gcc",), future_flag=True
        )
    ]
    assert commands == [
        ApkVirtualCommand(tag="build-deps", packages=("gcc",), present=True)
    ]


# --- apk.virtual: SimulatedHost seam dispatch --------------------------------


async def test_apk_virtual_add_seam_mutates_world_and_packages():
    """``send_command`` maps the create descriptor onto both seams the real
    ``apk add -t`` touches: the tag lands in ``apk_explicit_packages`` (sorted,
    de-duplicated) and the members in ``apk_packages``."""
    host = SimulatedHost(name="h", apk_explicit_packages=["curl"])
    await send_command(
        host, ApkVirtualCommand(tag="build-deps", packages=("gcc", "musl-dev"))
    )
    assert host.apk_explicit_packages == ["build-deps", "curl"]
    assert host.apk_packages == {"gcc": [""], "musl-dev": [""]}


async def test_apk_virtual_remove_seam_drops_tag():
    """The remove descriptor drops the tag from the world; a tag-only removal
    (no members named) leaves ``apk_packages`` untouched, as documented."""
    host = SimulatedHost(
        name="h",
        apk_explicit_packages=["build-deps", "curl"],
        apk_packages={"gcc": [""]},
    )
    await send_command(host, ApkVirtualCommand(tag="build-deps", present=False))
    assert host.apk_explicit_packages == ["curl"]
    assert host.apk_packages == {"gcc": [""]}


# --- apk.virtual: end to end -------------------------------------------------


async def test_apk_virtual_end_to_end_create_then_noop():
    """Through run_prepare/run_apply: the create executes under sudo and
    renders in the log; a second prepare sees the tag in the world and plans no
    change (the fresh per-step fact cache observes the mutation)."""

    def create_deploy(host):
        yield op(
            apk.virtual,
            name="Create build-deps",
            tag="build-deps",
            packages=("gcc", "musl-dev"),
            sudo=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], create_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (
        ApkVirtualCommand(tag="build-deps", packages=("gcc", "musl-dev"), present=True),
    )
    assert "$ sudo apk add -t build-deps gcc musl-dev" in format_log(report)
    assert host.apk_explicit_packages == ["build-deps"]
    # A second prepare sees the tag in the world: no change.
    _runtimes, plans2 = await run_prepare([host], create_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "virtual package build-deps already present"


async def test_apk_virtual_end_to_end_create_then_remove_cycle():
    """A create step then a remove step in one deploy: prepare plans both as
    changes (nothing is applied yet, so both compare against the same
    pre-deployment world), while apply creates on the first step and the fresh
    per-step cache lets the second see the tag present and remove it."""

    def cycle_deploy(host):
        yield op(
            apk.virtual,
            name="Create build-deps",
            tag="build-deps",
            packages=("gcc",),
            sudo=True,
        )
        yield op(
            apk.virtual,
            name="Remove build-deps",
            tag="build-deps",
            present=False,
            sudo=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], cycle_deploy)
    # Prepare sends nothing and reuses one fact cache: the world stays empty, so
    # the create is a change and the remove (tag absent) is planned unchanged.
    assert plans["h"][0].changed
    assert not plans["h"][1].changed
    report = await run_apply(runtimes, plans=plans)
    create_entry, remove_entry = report.log
    assert create_entry.changed
    # Apply recreates the cache per step: the create ran, so the remove step now
    # sees the tag present and removes it.
    assert remove_entry.changed
    assert "$ sudo apk del build-deps" in format_log(report)
    assert host.apk_explicit_packages == []


async def test_apk_virtual_dry_run_plans_without_sending():
    """``dry=True`` stops after prepare: the create is planned as a change but
    there is no apply report, so nothing runs on the host."""

    def create_deploy(host):
        yield op(
            apk.virtual,
            name="Create build-deps",
            tag="build-deps",
            packages=("gcc",),
            sudo=True,
        )

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        create_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert planned.commands == (
        ApkVirtualCommand(tag="build-deps", packages=("gcc",), present=True),
    )
    # Nothing ran: the dry run sent no command.
    assert host.apk_explicit_packages == []


# --- apk.hold: command descriptor --------------------------------------------


def test_apk_hold_command_renders_add_with_constraint():
    """The hold direction re-adds each name with its version constraint, which
    is how apk pins a package into ``/etc/apk/world``."""
    cmd = ApkHoldCommand(packages=("curl=8.14.0-r0", "nginx=1.2.3-r0"))
    assert cmd.shell() == "apk add curl=8.14.0-r0 nginx=1.2.3-r0"


def test_apk_hold_command_renders_bare_add_for_unhold():
    """The unhold direction re-adds each bare name (no constraint), which
    rewrites the world line without a bound and releases the pin -- the
    package stays installed."""
    cmd = ApkHoldCommand(packages=("curl", "nginx"), present=False)
    assert cmd.shell() == "apk add curl nginx"


def test_apk_hold_command_is_elevated_by_sudo():
    """Holding/releasing writes the root-owned apk database and world file, so
    the request's ``sudo=True`` prefixes the rendered shell (the run-as user
    comes from the inventory ``privilege``, never the operation)."""
    cmd = ApkHoldCommand(packages=("curl=8.14.0-r0",))
    assert command_shell(cmd) == "apk add curl=8.14.0-r0"
    assert command_shell(cmd, sudo=True) == "sudo apk add curl=8.14.0-r0"
    release = ApkHoldCommand(packages=("curl",), present=False)
    assert command_shell(release, su=True) == f"su -c {shlex.quote('apk add curl')}"


def test_apk_hold_command_shell_quotes_names():
    """The package specs are shell-quoted, so a metacharacter in a name can
    never reach the remote shell unescaped."""
    package = "curl`reboot`"
    cmd = ApkHoldCommand(packages=(package,))
    assert cmd.shell() == f"apk add {shlex.quote(package)}"
    assert shlex.quote(package) != package


# --- apk.hold: idempotence (version-aware hold state) ------------------------


async def test_apk_hold_pins_to_an_explicit_version():
    """With ``version`` given, the name is pinned to that constraint (a bare
    version normalized to ``=``) and one present=True command is yielded."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in apk.hold(ctx, packages=("curl",), version="8.14.0-r0")
    ]
    assert ctx.changed
    assert ctx.reason == "1 package(s) to hold"
    assert commands == [ApkHoldCommand(packages=("curl=8.14.0-r0",), present=True)]


async def test_apk_hold_keeps_an_operator_version_verbatim():
    """An operator-led ``version`` (``~1.2``, ``<2.0``) is passed through
    unchanged -- only a bare version gains the ``=`` pin."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apk.hold(ctx, packages=("curl",), version="~1.2")]
    assert commands == [ApkHoldCommand(packages=("curl~1.2",), present=True)]


async def test_apk_hold_pins_to_the_installed_version_by_default():
    """With no ``version``, each name is pinned to its currently installed
    version (``ApkPackages``' newest), read from the installed-version fact."""
    host = SimulatedHost(name="h", apk_packages={"nginx": ["1.2.3-r0"]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apk.hold(ctx, packages=("nginx",))]
    assert ctx.changed
    assert ctx.reason == "1 package(s) to hold"
    assert commands == [ApkHoldCommand(packages=("nginx=1.2.3-r0",), present=True)]


async def test_apk_hold_no_change_when_already_pinned():
    """A name already pinned to the wanted constraint reads as held, so
    nothing is yielded -- the reason is recorded even when unchanged."""
    host = SimulatedHost(name="h", apk_held_packages={"curl": "=8.14.0-r0"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in apk.hold(ctx, packages=("curl",), version="8.14.0-r0")
    ]
    assert not ctx.changed
    assert ctx.reason == "all packages held"
    assert commands == []


async def test_apk_hold_repins_when_the_constraint_differs():
    """A name pinned to a *different* constraint is re-held: version-aware
    idempotence detects the change the bare-name ``ApkExplicitPackages``
    could not."""
    host = SimulatedHost(name="h", apk_held_packages={"curl": "=8.13.0-r0"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in apk.hold(ctx, packages=("curl",), version="8.14.0-r0")
    ]
    assert ctx.changed
    assert ctx.reason == "1 package(s) to hold"
    assert commands == [ApkHoldCommand(packages=("curl=8.14.0-r0",), present=True)]


async def test_apk_hold_without_version_skips_an_uninstalled_name():
    """With no ``version``, a name that is not installed cannot be pinned, so
    it is reported but nothing is yielded and the operation is unchanged."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apk.hold(ctx, packages=("ghost",))]
    assert not ctx.changed
    assert ctx.reason == "1 package(s) not installed and no version given"
    assert commands == []


async def test_apk_hold_reports_held_and_unpinnable_together():
    """A request mixing a pinnable and an uninstalled name holds the one and
    reports the other -- both reasons are joined."""
    host = SimulatedHost(name="h", apk_packages={"nginx": ["1.2.3-r0"]})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apk.hold(ctx, packages=("nginx", "ghost"))]
    assert ctx.changed
    assert ctx.reason == (
        "1 package(s) to hold; 1 package(s) not installed and no version given"
    )
    assert commands == [ApkHoldCommand(packages=("nginx=1.2.3-r0",), present=True)]


async def test_apk_hold_accepts_a_bare_string():
    """``packages="curl"`` is shorthand for a one-item request, mirroring
    ``apt.mark`` and ``apk.packages``."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apk.hold(ctx, packages="curl", version="1.0-r0")]
    assert commands == [ApkHoldCommand(packages=("curl=1.0-r0",), present=True)]


async def test_apk_hold_empty_packages_is_a_noop():
    """An empty request pins nothing -- the same guard ``apt.mark`` keeps."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apk.hold(ctx, packages=[])]
    assert not ctx.changed
    assert ctx.reason == "no packages requested"
    assert commands == []


async def test_apk_hold_preserves_the_requested_order():
    """The command keeps the requested order (stable across phases), not the
    fact's or a set's order."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in apk.hold(
            ctx, packages=("zlib", "curl", "nginx"), version="1.0-r0"
        )
    ]
    assert commands == [
        ApkHoldCommand(
            packages=("zlib=1.0-r0", "curl=1.0-r0", "nginx=1.0-r0"), present=True
        )
    ]


async def test_apk_unhold_releases_held_names():
    """``present=False`` yields one bare-name command for the requested names
    that are currently held."""
    host = SimulatedHost(
        name="h", apk_held_packages={"curl": "=8.14.0-r0", "nginx": "=1.2.3-r0"}
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in apk.hold(ctx, packages=("curl", "nginx"), present=False)
    ]
    assert ctx.changed
    assert ctx.reason == "2 package(s) to unhold"
    assert commands == [ApkHoldCommand(packages=("curl", "nginx"), present=False)]


async def test_apk_unhold_only_releases_held_names():
    """A name that is not held is filtered out, so only the held ones are
    released."""
    host = SimulatedHost(name="h", apk_held_packages={"curl": "=8.14.0-r0"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in apk.hold(ctx, packages=("curl", "ghost"), present=False)
    ]
    assert ctx.changed
    assert ctx.reason == "1 package(s) to unhold"
    assert commands == [ApkHoldCommand(packages=("curl",), present=False)]


async def test_apk_unhold_noop_when_nothing_held():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apk.hold(ctx, packages=("curl",), present=False)]
    assert not ctx.changed
    assert ctx.reason == "no held packages to unhold"
    assert commands == []


async def test_apk_hold_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors pyinfra's global-arguments slot; op() already
    strips the ``name``/``sudo``/``su`` meta-parameters, the rest is ignored."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c
        async for c in apk.hold(
            ctx, packages=("curl",), version="1.0-r0", future_flag=True
        )
    ]
    assert commands == [ApkHoldCommand(packages=("curl=1.0-r0",), present=True)]


# --- apk.hold: SimulatedHost seam dispatch -----------------------------------


async def test_apk_hold_seam_pins_name_to_constraint():
    """``send_command`` maps the hold descriptor onto the seam: each spec's
    bare name and constraint land in ``apk_held_packages``, exactly as the
    fact parses the world line the real command writes."""
    host = SimulatedHost(name="h")
    await send_command(
        host, ApkHoldCommand(packages=("curl=8.14.0-r0", "nginx~1.2"), present=True)
    )
    assert host.apk_held_packages == {"curl": "=8.14.0-r0", "nginx": "~1.2"}


async def test_apk_unhold_seam_drops_the_pin():
    """The unhold descriptor pops the bare names from ``apk_held_packages``;
    an absent name is tolerated and the installed versions are untouched."""
    host = SimulatedHost(
        name="h",
        apk_held_packages={"curl": "=8.14.0-r0", "nginx": "=1.2.3-r0"},
        apk_packages={"curl": ["8.14.0-r0"]},
    )
    await send_command(host, ApkHoldCommand(packages=("curl", "ghost"), present=False))
    assert host.apk_held_packages == {"nginx": "=1.2.3-r0"}
    assert host.apk_packages == {"curl": ["8.14.0-r0"]}


# --- apk.hold: end to end ----------------------------------------------------


async def test_apk_hold_end_to_end_hold_then_noop():
    """Through run_prepare/run_apply: the hold executes under sudo and renders
    in the log; a second prepare sees the pin in the world and plans no change
    (the fresh per-step fact cache observes the mutation)."""

    def hold_deploy(host):
        yield op(
            apk.hold,
            name="Hold curl",
            packages=("curl",),
            version="8.14.0-r0",
            sudo=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], hold_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (
        ApkHoldCommand(packages=("curl=8.14.0-r0",), present=True),
    )
    assert "$ sudo apk add curl=8.14.0-r0" in format_log(report)
    assert host.apk_held_packages == {"curl": "=8.14.0-r0"}
    # A second prepare sees the pin: no change.
    _runtimes, plans2 = await run_prepare([host], hold_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "all packages held"


async def test_apk_hold_end_to_end_hold_then_unhold_cycle():
    """A hold step then an unhold step in one deploy: prepare plans the hold as
    a change and the unhold as none (nothing is applied yet, so the world is
    still unpinned), while apply pins on the first step and the fresh per-step
    cache lets the second see the pin and release it."""

    def cycle_deploy(host):
        yield op(
            apk.hold,
            name="Hold curl",
            packages=("curl",),
            version="8.14.0-r0",
            sudo=True,
        )
        yield op(
            apk.hold,
            name="Unhold curl",
            packages=("curl",),
            present=False,
            sudo=True,
        )

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], cycle_deploy)
    # Prepare sends nothing and reuses one fact cache: the world stays
    # unpinned, so the hold is a change and the unhold (nothing held) is not.
    assert plans["h"][0].changed
    assert not plans["h"][1].changed
    report = await run_apply(runtimes, plans=plans)
    hold_entry, unhold_entry = report.log
    assert hold_entry.changed
    assert hold_entry.commands == (
        ApkHoldCommand(packages=("curl=8.14.0-r0",), present=True),
    )
    # Apply recreates the cache per step: the hold ran, so the unhold step now
    # sees the pin and releases it.
    assert unhold_entry.changed
    assert unhold_entry.commands == (
        ApkHoldCommand(packages=("curl",), present=False),
    )
    assert host.apk_held_packages == {}


async def test_apk_hold_dry_run_plans_without_sending():
    """``dry=True`` stops after prepare: the hold is planned as a change but
    there is no apply report, so nothing runs on the host."""

    def hold_deploy(host):
        yield op(
            apk.hold,
            name="Hold curl",
            packages=("curl",),
            version="8.14.0-r0",
            sudo=True,
        )

    host = SimulatedHost(name="h")
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        hold_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert planned.commands == (
        ApkHoldCommand(packages=("curl=8.14.0-r0",), present=True),
    )
    # Nothing ran: the dry run sent no command.
    assert host.apk_held_packages == {}


# --- ApkAutoremovable: command parity ----------------------------------------


def test_apk_autoremovable_command_simulates_the_purge():
    """The command is apk's own orphan-purge simulation: ``apk add --purge
    --simulate``, with stderr redirected because an unprivileged run writes
    cache/index warnings there. The ``--simulate`` flag is what keeps the
    fact read-only -- the same shape ``AptAutoremovable`` uses with
    ``apt-get --just-print autoremove 2>/dev/null``."""
    assert ApkAutoremovable.COMMAND == "apk add --purge --simulate 2>/dev/null"


# --- ApkAutoremovable: real-transport parse ----------------------------------

# Realistic canned ``apk add --purge --simulate`` output: the leading solver
# summary, three ``(N/M) Purging`` rows carrying multi-dash and punctuation
# names (``alpine-baselayout-data``, ``libstdc++``), and the trailing
# ``OK:`` footer. Nothing in the summary or footer must reach the parse.
AUTOREMOVE_OUTPUT = """\
WARNING: Ignoring APKINDEX.00740ba1.tar.gz: No such file or directory
2 packages to purge, 0 new, 0 upgraded
(1/3) Purging py3-pycparser-2.22-r0
(2/3) Purging alpine-baselayout-data-3.6.5-r0
(3/3) Purging libstdc++-13.2.1_git20240309-r0
OK: 500 MiB in 100 packages
"""

AUTOREMOVE_FACT = ["alpine-baselayout-data", "libstdc++", "py3-pycparser"]


async def test_apk_autoremovable_parses_purge_rows():
    """Over the real-transport path, the ``(N/M) Purging`` rows parse into a
    sorted, de-duplicated bare-name list; the summary count, the ``OK:``
    footer and the ``WARNING:`` cache noise never match the row regex."""
    host = CannedShellHost(AUTOREMOVE_OUTPUT)
    assert await ApkAutoremovable.process(host) == AUTOREMOVE_FACT
    assert host.last_shell == ApkAutoremovable.COMMAND


async def test_apk_autoremovable_dedups_and_sorts():
    """Repeated rows collapse into one entry and the result is sorted, the
    same determinism ``AptAutoremovable`` guarantees."""
    output = (
        "(1/2) Purging zlib-1.2.11-r1\n"
        "(2/2) Purging zlib-1.2.11-r1\n"
        "(3/3) Purging curl-8.14.0-r0\n"
    )
    host = CannedShellHost(output)
    assert await ApkAutoremovable.process(host) == ["curl", "zlib"]


async def test_apk_autoremovable_skips_malformed_rows():
    """Rows without the ``(N/M)`` progress prefix, without a ``-r<release>``
    tail, or with an unknown verb are never mistaken for names -- the parser
    is strict so a future apk output change reads as ``[]`` rather than
    reporting garbage."""
    output = (
        "Purging noindex-1.0-r0\n"          # missing (N/M) prefix
        "(1/2) Purging noversion\n"           # missing -r<release> tail
        "(2/2) Installing foo-1.0-r0\n"       # wrong verb
        "random noise line\n"
        "\n"
    )
    host = CannedShellHost(output)
    assert await ApkAutoremovable.process(host) == []


async def test_apk_autoremovable_reads_empty_output_as_empty_list():
    """A simulation with nothing to purge (or a host without apk, whose
    non-zero exit the driver already surfaces separately) reads as ``[]``,
    matching the sibling ``AptAutoremovable``."""
    host = CannedShellHost("")
    assert await ApkAutoremovable.process(host) == []


async def test_apk_autoremovable_result_is_json_printable():
    """The returned list serializes with ``json.dumps`` directly, matching
    the fact-family contract."""
    host = CannedShellHost(AUTOREMOVE_OUTPUT)
    result = await ApkAutoremovable.process(host)
    assert json.loads(json.dumps(result)) == AUTOREMOVE_FACT


# --- ApkAutoremovable: SimulatedHost seam ------------------------------------


async def test_apk_autoremovable_reads_the_seam():
    """When the host carries the ``apk_autoremovable`` seam, the fact reads
    it verbatim and never shells out."""
    host = SimulatedHost(name="h", apk_autoremovable=["curl", "zlib"])
    assert await ApkAutoremovable.process(host) == ["curl", "zlib"]


async def test_apk_autoremovable_seam_copies_the_list():
    """The seam read returns a fresh list so a caller mutating it cannot
    reach into the simulated state."""
    host = SimulatedHost(name="h", apk_autoremovable=["curl"])
    first = await ApkAutoremovable.process(host)
    first.append("poison")
    assert host.apk_autoremovable == ["curl"]


async def test_apk_autoremovable_gather_fact_fans_out_over_hosts():
    """``gather_fact`` runs the fact's ``process`` concurrently across the
    inventory and returns a host-name-keyed mapping."""
    hosts = [
        SimulatedHost(name="a", apk_autoremovable=["curl"]),
        SimulatedHost(name="b", apk_autoremovable=[]),
        SimulatedHost(name="c", apk_autoremovable=["zlib", "openssl"]),
    ]
    result = await gather_fact(hosts, ApkAutoremovable)
    assert result == {"a": ["curl"], "b": [], "c": ["zlib", "openssl"]}


# --- apk.autoremove: command descriptor --------------------------------------


def test_apk_purge_command_renders_apk_add_purge():
    """The purge descriptor renders the bare ``apk add --purge`` -- no
    package arguments, no other flags -- so the solver drops every orphan
    and never installs or upgrades anything on the way."""
    assert ApkPurgeCommand().shell() == "apk add --purge"


def test_apk_purge_command_is_elevated_by_sudo():
    """Purging writes the root-owned apk database, so the request's
    ``sudo=True`` prefixes the rendered shell; ``su=True`` wraps it in
    ``su -c '<shell>'`` (the driver's su form, matching ``ApkHoldCommand``)."""
    cmd = ApkPurgeCommand()
    assert command_shell(cmd) == "apk add --purge"
    assert command_shell(cmd, sudo=True) == "sudo apk add --purge"
    assert command_shell(cmd, su=True) == f"su -c {shlex.quote('apk add --purge')}"


def test_apk_purge_command_has_no_arguments():
    """The descriptor is argument-less by design: the purge is a whole-system
    solver pass, not a per-name removal, so equality across phases is trivial
    and there is nothing for a caller to mis-parameterize."""
    assert ApkPurgeCommand() == ApkPurgeCommand()


# --- apk.autoremove: idempotence ---------------------------------------------


async def test_apk_autoremove_yields_when_the_fact_names_orphans():
    """A non-empty ``ApkAutoremovable`` yields one ``ApkPurgeCommand``; the
    reason lists the sorted names, identical across phases."""
    host = SimulatedHost(
        name="h", apk_autoremovable=["curl", "openssl", "zlib"]
    )
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apk.autoremove(ctx)]
    assert ctx.changed
    assert ctx.reason == (
        "purgable packages will be removed: curl, openssl, zlib"
    )
    assert commands == [ApkPurgeCommand()]


async def test_apk_autoremove_noop_when_nothing_to_purge():
    """An empty ``ApkAutoremovable`` is a no-op: nothing is yielded and the
    reason records the empty state."""
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apk.autoremove(ctx)]
    assert not ctx.changed
    assert ctx.reason == "no purgable packages"
    assert commands == []


async def test_apk_autoremove_absorbs_unknown_kwargs():
    """``**kwargs`` mirrors pyinfra's global-arguments slot; ``op()`` already
    strips the ``name``/``sudo``/``su`` meta-parameters, the rest is
    ignored -- including apt's ``wait_for_lock``, which apk deliberately
    does not offer."""
    host = SimulatedHost(name="h", apk_autoremovable=["curl"])
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [
        c async for c in apk.autoremove(ctx, wait_for_lock=60, future_flag=True)
    ]
    assert commands == [ApkPurgeCommand()]


# --- apk.autoremove: SimulatedHost seam dispatch -----------------------------


async def test_apk_autoremove_seam_clears_the_fact_and_pops_installed():
    """``send_command`` maps the purge descriptor onto the seam: every
    ``apk_autoremovable`` name is popped from ``apk_packages`` (so
    ``ApkPackages`` no longer reports it) and the fact itself is cleared (so
    a repeat purge reads empty)."""
    host = SimulatedHost(
        name="h",
        apk_autoremovable=["curl", "zlib"],
        apk_packages={
            "curl": ["8.14.0-r0"],
            "zlib": ["1.2.11-r1"],
            "busybox": ["1.36.1-r29"],
        },
    )
    await send_command(host, ApkPurgeCommand())
    assert host.apk_autoremovable == []
    assert host.apk_packages == {"busybox": ["1.36.1-r29"]}


async def test_apk_autoremove_seam_leaves_world_and_holds_untouched():
    """An orphan is by definition not a world entry, and a held package
    carries a version constraint in the world so it is never an orphan: the
    purge mutator therefore does not touch ``apk_explicit_packages`` or
    ``apk_held_packages``."""
    host = SimulatedHost(
        name="h",
        apk_autoremovable=["orphan"],
        apk_packages={"orphan": ["1.0-r0"], "curl": ["8.14.0-r0"]},
        apk_explicit_packages=["busybox", "curl"],
        apk_held_packages={"curl": "=8.14.0-r0"},
    )
    await send_command(host, ApkPurgeCommand())
    assert host.apk_explicit_packages == ["busybox", "curl"]
    assert host.apk_held_packages == {"curl": "=8.14.0-r0"}
    assert host.apk_packages == {"curl": ["8.14.0-r0"]}


# --- apk.autoremove: end to end ----------------------------------------------


async def test_apk_autoremove_end_to_end_purge_then_noop():
    """Through run_prepare/run_apply: the purge executes under sudo and
    renders in the log; a second prepare sees an empty ``ApkAutoremovable``
    and plans no change (the fresh per-step fact cache observes the
    mutation)."""

    def autoremove_deploy(host):
        yield op(apk.autoremove, name="Purge orphans", sudo=True)

    host = SimulatedHost(
        name="h",
        apk_autoremovable=["curl", "zlib"],
        apk_packages={
            "curl": ["8.14.0-r0"],
            "zlib": ["1.2.11-r1"],
            "busybox": ["1.36.1-r29"],
        },
    )
    runtimes, plans = await run_prepare([host], autoremove_deploy)
    assert plans["h"][0].changed
    report = await run_apply(runtimes, plans=plans)
    (entry,) = report.log
    assert entry.changed
    assert entry.commands == (ApkPurgeCommand(),)
    assert "$ sudo apk add --purge" in format_log(report)
    assert host.apk_autoremovable == []
    assert host.apk_packages == {"busybox": ["1.36.1-r29"]}
    # A second prepare sees nothing to purge.
    _runtimes, plans2 = await run_prepare([host], autoremove_deploy)
    assert not plans2["h"][0].changed
    assert plans2["h"][0].reason == "no purgable packages"


async def test_apk_autoremove_dry_run_plans_without_sending():
    """``dry=True`` stops after prepare: the purge is planned as a change
    but nothing runs on the host, so the seam still names the orphans."""

    def autoremove_deploy(host):
        yield op(apk.autoremove, name="Purge orphans", sudo=True)

    host = SimulatedHost(
        name="h",
        apk_autoremovable=["curl"],
        apk_packages={"curl": ["8.14.0-r0"]},
    )
    plans, report, _hosts = await run(
        [{"host": "h", "connection": {}}],
        autoremove_deploy,
        host_factory=lambda entry: host,
        dry=True,
    )
    assert report is None
    (planned,) = plans["h"]
    assert planned.changed
    assert planned.commands == (ApkPurgeCommand(),)
    # Nothing ran: the dry run sent no command.
    assert host.apk_autoremovable == ["curl"]
    assert host.apk_packages == {"curl": ["8.14.0-r0"]}
