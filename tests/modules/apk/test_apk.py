"""Tests for the apk module: ``ApkExplicitPackages``, ``ApkPackages`` and
``ApkRepositories``.

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

``asyncio_mode = "auto"`` lets the async tests below run without decorators.
"""

import json

from reemote5.drivers import gather_fact
from reemote5.hosts import Host
from reemote5.modules.apk.apk import (
    ApkExplicitPackages,
    ApkPackage,
    ApkPackages,
    ApkRepositories,
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
