# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``apk`` module: facts in one source.

Fact side: ``ApkPackages``, the installed Alpine Linux (apk) packages with
their versions, parsed from ``apk list --installed`` rows,
``ApkExplicitPackages``, the explicitly installed (world) package names
parsed from ``/etc/apk/world``, ``ApkRepositories``, the configured apk
repositories parsed from ``/etc/apk/repositories``, and ``ApkPackage``, one
package's detailed metadata (version, description, size, dependencies,
provides) parsed from the labelled sections of ``apk info -a``. Like every
fact they are phase-unaware: their ``process`` coroutine just gathers the
raw value.

Operation side: none yet -- pyinfra's ``apk`` operations (``apk.packages``,
``apk.upgrade``, ``apk.key``, ``apk.repo``) are not migrated, so this
namespace is fact-only so far.
"""

import re
import shlex
from collections.abc import Iterable
from typing import ClassVar, TypedDict

from ...hosts import Host

# --- Shared helpers ----------------------------------------------------------


def _to_int(value: str) -> int:
    """The ``int`` an ``apk info`` size line prints, or ``0`` when it is absent.

    apk's ``installed size`` section carries a bare byte count; an absent or
    unparseable value reads as ``0`` so the fact stays total and
    ``json.dumps``-printable without a per-field branch in ``parse``.
    """
    try:
        return int(value)
    except ValueError:
        return 0


# --- Facts -------------------------------------------------------------------


class ApkExplicitPackages:
    """The explicitly installed apk packages, as a sorted list of names.

    A reemote5 original: pyinfra's ``apk`` namespace ships only
    ``ApkPackages``, so nothing upstream can separate the packages an
    operator asked for from the ones apk pulled in as dependencies. apk
    records the former -- its *world* -- in ``/etc/apk/world``, one
    dependency constraint per line in ``[!]name{@tag}{[<>~=]version}``
    notation, and each line's bare package name becomes one entry, for
    example::

        ["busybox", "curl", "openssh"]

    Names are de-duplicated and sorted -- the same shape the sibling
    name-list facts ``apt.AptHeldPackages`` and ``apt.AptAutoremovable``
    report, and the name-only half of what ``dnf.DnfPackages`` returns --
    so the result compares directly against ``ApkPackages``' keys: the
    installed names missing from this list are the dependency-only ones an
    audit is after (apk has no autoremove, so cleaning up stays an explicit
    operator decision, never a fact side effect). Each line's constraint is
    deliberately dropped, exactly as ``AptAutoremovable`` drops the version
    its simulation rows carry: the ``@tag`` a package may be selected from
    and the ``=``, ``<``, ``>``, ``>=``, ``~`` or ``><`` version bound it is
    pinned to say nothing about *which* packages were asked for. A leading
    ``!`` marks a conflict constraint -- a package that must NOT be
    installed -- so it never contributes a name. Blank lines and ``#``
    comments are skipped and a line carrying no name (a lone operator or
    tag) is ignored, the way the siblings skip unparseable rows. A missing
    or unreadable file -- any host without apk -- reads as ``[]`` through
    the command's ``|| true``, like ``ApkRepositories`` and
    ``DnfRepositories`` tolerate their missing files. The name follows the
    sibling facts of the package-list family rather than the file it reads
    (``ApkWorld`` would name a file, not answer a question) and keeps
    apk's own vocabulary -- apk documents these as the *explicitly*
    installed packages, where apt calls them ``manual`` and dnf
    ``userinstalled``.
    """

    #: The file ``apk`` keeps its world -- the explicitly installed package
    #: constraints -- in: one ``[!]name{@tag}{[<>~=]version}`` per line.
    PATH = "/etc/apk/world"

    #: Cat the world file, tolerating its absence: the ``|| true`` keeps a
    #: missing file (a non-Alpine host, or ``cat``'s error on stderr) from
    #: failing the gather, so the fact reads as ``[]`` -- the same command
    #: shape ``ApkRepositories`` builds for ``/etc/apk/repositories``.
    COMMAND = f"cat -- {PATH} 2>/dev/null || true"

    #: The characters that start a world constraint's repository tag or
    #: version bound: the first of them ends the package name, so
    #: everything before it is the name and everything from it on is the
    #: constraint the parser drops.
    BOUNDARY_REGEX = re.compile(r"[@<>~=]")

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[str]:
        """Parse ``/etc/apk/world`` lines into sorted, unique names.

        Comments (``#``) and blank lines are skipped; so is a conflict
        constraint (a line starting with ``!``), which names a package that
        must not be installed. Each remaining line contributes the text up
        to the first tag or version-boundary character, stripped; a line
        whose name is empty is not a package constraint and is ignored.
        Duplicates collapse into one entry and the result is sorted. Pure
        and testable.
        """
        names: set[str] = set()
        for raw in output:
            line = raw.strip()
            if not line or line.startswith(("#", "!")):
                continue
            name = cls.BOUNDARY_REGEX.split(line, maxsplit=1)[0].strip()
            if name:
                names.add(name)
        return sorted(names)

    @classmethod
    async def process(cls, host: Host) -> list[str]:
        apk_explicit_packages = getattr(host, "apk_explicit_packages", None)
        if apk_explicit_packages is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy
            # the list so callers never mutate simulated state.
            return list(apk_explicit_packages)
        # Real transport: cat the world file over asyncssh and parse.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class ApkPackageInfo(TypedDict):
    """One apk package's detailed metadata (``ApkPackage``'s entry).

    A TypedDict rather than a class instance, like ``AptPackagePolicy``:
    entries are ordinary dicts, so equality against a literal dict works and
    ``json.dumps`` serializes them directly.

    Attributes:
        name: The package name, e.g. ``"curl"``.
        version: The version-and-release apk reports, e.g. ``"8.14.0-r0"``
            -- the same shape ``ApkPackages`` keys its version lists by.
        description: The one-line package description (apk's ``description``).
        url: The upstream homepage (apk's ``webpage`` field), ``""`` when the
            package prints none.
        license: The SPDX license identifier (apk's ``license``), ``""`` when
            apk prints none.
        size: The installed size in bytes (apk's ``installed size``), ``0``
            when apk prints none.
        depends: The package's dependency strings (apk's ``depends on`` rows),
            in apk's printed order.
        provides: The virtual names the package provides (apk's ``provides``
            rows), in apk's printed order.
    """

    name: str
    version: str
    description: str
    url: str
    license: str
    size: int
    depends: list[str]
    provides: list[str]


class ApkPackage:
    """Detailed information about one specific apk package.

    A reemote5 original: pyinfra's ``apk`` namespace ships only
    ``ApkPackages``, so nothing upstream reports a single package's
    dependencies, size or description -- the detail a version comparison or a
    dependency check needs. ``apk info -a <package>`` prints every field apk
    knows about the package, each as a ``<name>-<version> <field>:`` header
    followed by its value lines and a blank line; the tracked fields become
    one ``ApkPackageInfo`` dict, for example::

        {
            "name": "curl",
            "version": "8.14.0-r0",
            "description": "URL retrieval utility and library",
            "url": "https://curl.se/",
            "license": "curl",
            "size": 1085898,
            "depends": ["so:libc.musl-x86_64.so.1", "so:libz.so.1"],
            "provides": ["curl-doc"],
        }

    The ``name`` and ``version`` come from the header token, split at the last
    ``-r<release>`` boundary exactly as ``ApkPackages`` splits its listing
    rows, so multi-dash names (``alpine-baselayout-data``) and punctuation
    (``libstdc++``) stay intact. apk spells two labels differently from the
    result keys (``webpage`` -> ``url``, ``installed size`` -> ``size``,
    ``depends on`` -> ``depends``); the remaining ``-a`` fields apk prints
    (``contains``, ``is required by``, ``triggers``, ``install if``,
    ``replaces``) are deliberately not tracked -- the file listing and reverse
    dependencies belong to other questions, not to this package's own
    metadata. A scalar field whose section is absent reads as its empty
    default (``""`` or ``0``); ``depends`` and ``provides`` default to ``[]``.
    An absent package -- or a host without apk -- prints no field header (the
    command's ``2>/dev/null || true`` swallows the non-zero exit), so
    ``process`` reports ``None``, mirroring ``apt.DebPackage`` and
    ``dnf.RpmPackage``'s None-on-absent convention rather than pyinfra's
    empty-dict answer. This fact fills a coverage gap of ``ApkPackages``:
    the ``apk list --installed`` rows that fact parses report only whether a
    name is installed and at which version, never its dependencies, size or
    description, so a deploy verifying a specific package's detail reads it
    here. Note one deliberate gap against the sibling ``apt.AptPolicy`` (which
    surfaces each version's repository origins): apk's ``apk info`` prints no
    repository field at all, so this fact reports the package's own metadata
    only -- the ``{origin}`` source package of every installed name is what
    ``ApkPackages`` parses, and the configured repositories are what
    ``ApkRepositories`` reports.
    """

    #: Matches an ``apk info`` field header: the ``name-version`` token, then
    #: the field label up to the trailing colon. The version-and-release group
    #: is ``\S+-r\S+`` (apk versions carry no spaces), so it cannot swallow the
    #: spaces of a multi-word label such as ``Installed size``; the greedy name
    #: group still splits at the last ``-r<release>`` boundary, exactly as
    #: ``ApkPackages`` splits its rows. apk capitalizes most labels
    #: (``Description``, ``Installed size``) but spells one lower-case (``is
    #: required by``), so the label is matched case-insensitively and
    #: lower-cased before the ``FIELD_KEYS`` lookup.
    HEADER_REGEX = re.compile(r"^(.+)-(\S+-r\S+) ([A-Za-z][A-Za-z ]*):$")

    #: The headers whose value lines are collected, mapped to their result
    #: key. apk spells ``webpage``/``installed size``/``depends on``
    #: differently from the ``url``/``size``/``depends`` keys; a label absent
    #: from this map (``contains``, ``is required by``, ``triggers``, ...) is
    #: not a tracked field and its lines are skipped.
    FIELD_KEYS: ClassVar[dict[str, str]] = {
        "description": "description",
        "webpage": "url",
        "license": "license",
        "installed size": "size",
        "depends on": "depends",
        "provides": "provides",
    }

    #: Tracked fields whose value is a list of lines rather than one scalar.
    LIST_FIELDS = frozenset({"depends", "provides"})

    @classmethod
    def command(cls, package: str) -> str:
        """Shell reporting ``package``'s detailed metadata over SSH.

        ``apk info -a`` prints every field apk knows about the package. The
        ``package`` argument is ``shlex``-quoted, and the ``2>/dev/null ||
        true`` tail swallows the non-zero exit an absent package (or a host
        without apk) produces, so nothing reaches stdout and ``process``
        reports ``None`` -- the ``apt.DebPackage`` tolerance rather than
        pyinfra's ``requires_command`` gating.
        """
        return f"apk info -a {shlex.quote(package)} 2>/dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> ApkPackageInfo | None:
        """Parse ``apk info -a`` field sections into an ``ApkPackageInfo``.

        Any line matching ``HEADER_REGEX`` closes the field being collected and
        supplies the ``name``/``version`` from its header token (every header of
        one package carries the same ``name-version``). When its label is in
        ``FIELD_KEYS`` the section is tracked: every following non-blank line is
        that field's value -- a scalar keeps the line, a list field (``depends``
        /``provides``) collects it. A header whose label is untracked (``contains``,
        ``is required by``, ...) closes the previous field but collects nothing
        itself; blank lines contribute nothing. Returns ``None`` when no tracked
        header appears -- an absent package printed nothing. Pure and testable.
        """
        name = ""
        version = ""
        scalars: dict[str, str] = {}
        lists: dict[str, list[str]] = {"depends": [], "provides": []}
        current: str | None = None
        found = False
        for raw in output:
            line = raw.strip()
            if match := cls.HEADER_REGEX.match(line):
                # Any header starts a new section, so the current field stops
                # collecting here -- an untracked section (``contains``, ``is
                # required by``, ...) must not leak its lines into the
                # previously tracked field. Every header carries the same
                # ``name-version``, so it always refreshes them; only a tracked
                # one marks the package as present.
                label = match.group(3).lower()
                name, version = match.group(1), match.group(2)
                current = cls.FIELD_KEYS.get(label)
                if current is not None:
                    found = True
                continue
            if not line or current is None:
                continue
            if current in cls.LIST_FIELDS:
                lists[current].append(line)
            else:
                scalars[current] = line
        if not found:
            return None
        return {
            "name": name,
            "version": version,
            "description": scalars.get("description", ""),
            "url": scalars.get("url", ""),
            "license": scalars.get("license", ""),
            "size": _to_int(scalars.get("size", "")),
            "depends": lists["depends"],
            "provides": lists["provides"],
        }

    @classmethod
    async def process(cls, host: Host, package: str) -> ApkPackageInfo | None:
        apk_package_info = getattr(host, "apk_package_info", None)
        if apk_package_info is not None:
            # SimulatedHost seam: the package-keyed store holds the parsed
            # answers; a name absent from it reads as None, like an absent
            # package. Copy the dict and its list fields so callers never
            # mutate simulated state.
            info = apk_package_info.get(package)
            if info is None:
                return None
            return {**info, "depends": list(info["depends"]), "provides": list(info["provides"])}
        # Real transport: query the package's metadata over asyncssh.
        stdout = await host.run_shell(cls.command(package))
        return cls.parse(stdout.splitlines())


class ApkPackages:
    """Installed apk packages, as a dict of name -> sorted version list.

    Mirrors pyinfra's ``apk.ApkPackages``: ``apk list --installed`` rows
    (``name-version arch {origin} (license) [installed]``) are matched with
    pyinfra's apk regex -- the Superuser-derived pattern that keeps version
    and release inside one group -- so multi-dash names like
    ``alpine-baselayout-data`` split from their ``3.6.5-r0``-style versions
    at the last ``-r<release>`` boundary, names carrying punctuation
    (``libstdc++``, ``p11-kit``, ``json-c``) stay intact, and the
    ``WARNING: Ignoring APKINDEX...: No such file or directory`` index-noise
    lines never match, for example::

        {
            "apk-tools": ["2.10.4-r2"],
            "musl": ["1.1.22-r3"],
            "zlib": ["1.2.11-r1"],
        }

    Versions are de-duplicated and sorted: pyinfra's ``parse_packages``
    collects them into a set per name, while reemote5 returns the sorted
    list so reads stay deterministic (and JSON-printable) -- the same shape
    ``apt.AptPackages`` and ``dnf.DnfPackages`` report. pyinfra's
    ``requires_command`` gating on ``apk`` is deliberately dropped: a
    missing ``apk`` exits 127, which ``run_shell`` reports as
    ``CommandNotFoundError``. Which of these packages the operator asked
    for, rather than one apk pulled in as a dependency, is not visible in
    the rows either: ``ApkExplicitPackages`` reports the world names. The
    rows give no single package's dependencies, size or description either:
    ``ApkPackage`` queries one package's detail directly.
    """

    #: Shell listing installed packages over SSH (pyinfra's
    #: ``ApkPackages.command``). ``apk list --installed`` prints one
    #: ``name-version arch {origin} (license) [installed]`` row per package.
    COMMAND = "apk list --installed"

    #: pyinfra's apk-row regex: the greedy name group stops at the last
    #: ``-<version>-r<release>`` boundary (version and release stay one
    #: group), followed by the architecture, the ``{origin}`` package
    #: name and the parenthesized licenses -- the tail that keeps noise
    #: lines like the ``WARNING:`` index messages from matching.
    APK_REGEX = r"(.+)-([^-]+-r[^-]+) \S+ \{\S+\} \(.+?\)"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, list[str]]:
        """Parse ``apk list --installed`` rows into name -> sorted versions.

        Each matching line contributes its version-and-release to the
        name's set (pyinfra's ``parse_packages`` de-dup); non-matching
        lines -- the ``WARNING:`` index noise, blank lines, rows without
        the ``{origin} (license)`` tail -- are skipped. The sets are
        returned as sorted lists. Pure and testable.
        """
        packages: dict[str, set[str]] = {}
        for line in output:
            matches = re.match(cls.APK_REGEX, line)
            if matches:
                packages.setdefault(matches.group(1), set()).add(matches.group(2))
        return {name: sorted(versions) for name, versions in packages.items()}

    @classmethod
    async def process(cls, host: Host) -> dict[str, list[str]]:
        apk_packages = getattr(host, "apk_packages", None)
        if apk_packages is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy
            # the version lists so callers never mutate simulated state.
            return {name: list(versions) for name, versions in apk_packages.items()}
        # Real transport: list installed packages over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class ApkRepositories:
    """The configured apk repositories, as a list of ``{url, tag}`` dicts.

    A reemote5 original: pyinfra's ``apk`` namespace ships no repositories
    fact (only ``ApkPackages``), so ``/etc/apk/repositories`` cannot be
    audited or conditionally modified through pyinfra facts. Each
    non-comment line names one repository -- an optional ``@tag``
    (``@testing``-style, for a repository referenced by ``apk add --repository``
    or ``/etc/apk/world`` tags) followed by the URL -- and becomes one dict
    with the ``url`` and its ``tag`` (``""`` when untagged), for example::

        [
            {"url": "https://dl-cdn.alpinelinux.org/alpine/v3.19/main", "tag": ""},
            {"url": "https://dl-cdn.alpinelinux.org/alpine/edge/testing", "tag": "testing"},
        ]

    Comments (``#``) and blank lines are ignored and entries keep file
    order -- no dedup, no sorting -- like ``apt.AptSources`` and
    ``dnf.DnfRepositories``, the two sibling facts this one mirrors in shape
    and command style. A line that does not carry exactly an optional tag
    and a URL is skipped, the way the siblings ignore unparseable rows.
    A missing or unreadable file -- any host without apk -- reads as ``[]``
    through the command's ``|| true``, like ``DnfRepositories`` tolerates
    missing repo files (pyinfra's ``default = list``, had this fact existed
    upstream). The return shape deliberately differs in size, not kind,
    from the siblings: apk's one-line format carries only a URL and an
    optional tag, where apt entries hold distribution/components/options
    and dnf entries hold every INI option.
    """

    #: The file ``apk`` reads its repository list from -- one optional
    #: ``@tag URL`` (or bare URL) per line, ``#`` for comments.
    PATH = "/etc/apk/repositories"

    #: Cat the repositories file, tolerating its absence: the ``|| true``
    #: keeps a missing file (a non-Alpine host, or ``cat``'s error on
    #: stderr) from failing the gather, so the fact reads as ``[]`` -- the
    #: same no-root-marker tolerance ``DnfRepositories``' command builds in.
    COMMAND = f"cat -- {PATH} 2>/dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[dict[str, str]]:
        """Parse ``/etc/apk/repositories`` lines into ``{url, tag}`` dicts.

        ``#`` comments and blank lines are skipped; a bare URL line gets
        ``tag`` ``""`` and a ``@tag URL`` line gets the tag without its
        leading ``@``; any other shape (a lone ``@tag``, several URLs on
        one line) is not a repository line and is ignored. Entries keep
        file order. Pure and testable.
        """
        repositories: list[dict[str, str]] = []
        for raw in output:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            tokens = line.split()
            if tokens[0].startswith("@"):
                if len(tokens) != 2:
                    continue
                tag, url = tokens[0][1:], tokens[1]
            elif len(tokens) == 1:
                tag, url = "", tokens[0]
            else:
                continue
            repositories.append({"url": url, "tag": tag})
        return repositories

    @classmethod
    async def process(cls, host: Host) -> list[dict[str, str]]:
        apk_repositories = getattr(host, "apk_repositories", None)
        if apk_repositories is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy
            # each dict so callers never mutate simulated state.
            return [dict(repo) for repo in apk_repositories]
        # Real transport: cat the repositories file over asyncssh and parse.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


__all__ = [
    "ApkExplicitPackages",
    "ApkPackage",
    "ApkPackageInfo",
    "ApkPackages",
    "ApkRepositories",
]
