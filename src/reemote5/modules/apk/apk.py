# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""The ``apk`` module: facts in one source.

Fact side: ``ApkPackages``, the installed Alpine Linux (apk) packages with
their versions, parsed from ``apk list --installed`` rows,
``ApkExplicitPackages``, the explicitly installed (world) package names
parsed from ``/etc/apk/world``, ``ApkHeldPackages``, the version-pinned
(held) packages and their constraints parsed from the same ``/etc/apk/world``,
``ApkAutoremovable``, the orphan packages ``apk add --purge`` would remove
parsed from its ``--simulate`` rows, ``ApkRepositories``, the configured apk
repositories parsed from ``/etc/apk/repositories``, and ``ApkPackage``, one
package's detailed metadata (version, description, size, dependencies,
provides) parsed from the labelled sections of ``apk info -a``. Like every
fact they are phase-unaware: their ``process`` coroutine just gathers the
raw value.

Operation side: ``apk.packages`` plus the typed command descriptors it
yields (``ApkInstallCommand``/``ApkRemoveCommand``/``ApkUpgradeCommand``).
``apk.packages`` mirrors pyinfra's ``apk.packages`` (add/remove packages
with version pins and ``latest`` upgrades), reading ``ApkPackages``, and
exposes two apk-native install modifiers -- ``no_cache``
(``apk add --no-cache``, bypass the package cache) and ``no_scripts``
(``apk add --no-scripts``, skip post-install triggers) -- which apt and
dnf have no equivalent for, so they are a deliberate apk-only asymmetry
on the shared package-manager surface;
pyinfra's ``update`` and ``upgrade`` prefix options are deliberately
dropped -- reemote5 keeps the whole-system ``apk update``/``apk upgrade``
out of the package operation, and both keywords are absorbed by
``**kwargs`` with no effect. The remaining pyinfra ``apk`` operations
(``apk.upgrade``, ``apk.key``, ``apk.repo``) are not migrated.
``apk.virtual`` -- a reemote5 original with no pyinfra counterpart --
manages a *virtual package* group (``apk add -t <tag>`` to create it,
``apk del <tag>`` to remove it) through its present-flagged
``ApkVirtualCommand``, reading ``ApkExplicitPackages`` for world-name
idempotence. ``apk.hold`` -- the apk counterpart of ``apt.mark`` -- pins
packages back from upgrades by writing a version constraint into
``/etc/apk/world`` (``apk add <pkg>=<version>`` to hold, ``apk add <pkg>``
to release the pin) through its present-flagged ``ApkHoldCommand``, reading
``ApkHeldPackages`` for version-aware idempotence. ``apk.autoremove`` -- the
apk counterpart of ``apt.auto_remove``, and a reemote5 original: pyinfra's
``apk`` namespace has no cleanup verb, and apk itself ships no
``autoremove`` subcommand -- purges the orphan packages nothing in
``/etc/apk/world`` reaches any more through its ``ApkPurgeCommand``
(``apk add --purge``), reading ``ApkAutoremovable`` for idempotence.
Operations are async generators that receive an ``OpContext``,
await facts, set ``ctx.changed``/``ctx.reason``, and yield command
descriptors; they never touch the host directly.
"""

import re
import shlex
from collections.abc import AsyncGenerator, Iterable
from dataclasses import dataclass
from typing import ClassVar, TypedDict

from ...commands import Command
from ...core import OpContext
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


#: The characters that begin an apk world constraint's version bound --
#: ``=``, ``<``, ``>`` and ``~`` (apk's ``>=``/``>~``/``<~``/``><`` are these
#: doubled). The repository ``@tag`` is deliberately absent: selecting a
#: package from a tagged repository is not a version *hold*, so a ``@tag``
#: line carrying no version bound never counts as held.
_VERSION_OP_REGEX = re.compile(r"[<>~=]")


def _split_constraint(package: str) -> tuple[str, str]:
    """Split ``name{[<>~=]version}`` into ``(name, constraint)``.

    The constraint keeps its leading operator (``=8.14.0-r0``, ``>~9.7``),
    so a bare name reads as ``(name, "")``. The split is at the *first*
    version operator, found with a regex search on a character class --
    never ``str.split`` on a multi-character delimiter, which would silently
    mis-parse -- and the ``@tag`` a name may carry is left inside the name
    half (``_VERSION_OP_REGEX`` does not match ``@``); callers that want the
    bare name split it off with ``ApkHeldPackages.BOUNDARY_REGEX``.
    """
    match = _VERSION_OP_REGEX.search(package)
    if match is None:
        return package, ""
    index = match.start()
    return package[:index], package[index:]


def _normalize_constraint(version: str) -> str:
    """The world constraint a ``version`` argument pins to.

    apk's ``apk add <pkg><constraint>`` writes ``<pkg><constraint>`` into
    ``/etc/apk/world``, and a constraint with no operator means ``=``: a
    bare ``1.2.3-r0`` pins exactly, while an operator-led ``~1.2`` or
    ``<2.0`` is passed through verbatim. The first character decides, so a
    ``version`` already carrying ``=``, ``<``, ``>`` or ``~`` is unchanged.
    """
    return version if version[:1] in ("<", ">", "=", "~") else f"={version}"


# --- Facts -------------------------------------------------------------------


class ApkAutoremovable:
    """Orphan packages ``apk add --purge`` would remove, as a sorted name list.

    The apk counterpart of ``apt.AptAutoremovable`` and a reemote5 original:
    pyinfra's ``apk`` namespace ships no cleanup verb, and apk itself has no
    ``autoremove`` subcommand -- but it does have a first-class orphan-purge
    mechanism, the ``--purge`` flag of ``apk add``/``apk fix``. Running
    ``apk add --purge`` with no packages asks apk's solver to remove every
    installed package nothing in ``/etc/apk/world`` reaches any more, and
    adding ``--simulate`` prints the same plan without touching the system,
    one ``(N/M) Purging <name>-<version>`` row per orphan, for example::

        ["libllvm17", "py3-pycparser", "py3-pycparser-pyc"]

    Only the ``Purging`` rows are read: the leading ``N packages to purge``
    summary, the trailing ``OK: <size> in <count> packages`` footer, the
    progress lines and blank lines are never mistaken for package names, and
    each row's version-and-release is dropped at the same last
    ``-<version>-r<release>`` boundary ``ApkPackages`` splits its listing
    rows at -- the names are what a cleanup audits, not the versions apk
    happened to have installed. Nothing purgable (or no output at all) reads
    as ``[]``, exactly as ``AptAutoremovable``' empty simulation does.
    This fact fills a coverage gap of ``ApkPackages``: the ``apk list
    --installed`` rows that fact parses cannot tell an orphaned dependency
    from one still reachable from the world, so a deploy cleaning up after
    itself reads the purgable names here -- and only collects the list;
    removal stays an explicit operator decision, never a fact side effect --
    the ``apk.autoremove`` operation performs that cleanup. The simulation
    needs no root, and a missing ``apk`` exits 127, which ``run_shell``
    reports as ``CommandNotFoundError``.

    One deliberate deviation from ``AptAutoremovable``: apk's ``--simulate``
    output is human-readable rather than machine-readable, so the row format
    is less stable than ``apt-get --just-print autoremove``'s. The same
    pragmatic trade-off reemote5 already accepts for apt's ``Remv`` rows --
    the parser is strict about the shape it recognises and simply skips a row
    whose format changed, so a future apk release reads as ``[]`` rather
    than reporting garbage.
    """

    #: Shell simulating the orphan purge over SSH, one ``(N/M) Purging`` row
    #: per package that would go. ``--simulate`` never removes anything (the
    #: fact only collects the list), and ``2>/dev/null`` suppresses the
    #: cache/index warnings an unprivileged simulation writes to stderr --
    #: the same redirect ``AptAutoremovable``'s command builds in.
    COMMAND = "apk add --purge --simulate 2>/dev/null"

    #: A simulation purge row: the ``(N/M)`` progress prefix, the ``Purging``
    #: token, then ``name-version-release`` with the version tail dropped at
    #: the last ``-<version>-r<release>`` boundary ``ApkPackages`` splits its
    #: rows at -- so ``alpine-baselayout-data-3.6.5-r0`` keeps its multi-dash
    #: name and ``libstdc++-13.2.1_git20240309-r0`` its punctuation intact.
    PURGE_REGEX = re.compile(r"^\(\d+/\d+\) Purging (.+)-[^-]+-r[^-]+$")

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[str]:
        """Parse ``apk add --purge --simulate`` rows into sorted, unique names.

        Only lines matching ``PURGE_REGEX`` contribute their package name;
        every other line -- the summary count, the ``OK:`` footer, progress
        output, blank lines -- is skipped. Duplicates collapse into one entry
        and the result is sorted. Pure and testable.
        """
        names: set[str] = set()
        for line in output:
            matches = cls.PURGE_REGEX.match(line.strip())
            if matches:
                names.add(matches.group(1))
        return sorted(names)

    @classmethod
    async def process(cls, host: Host) -> list[str]:
        apk_autoremovable = getattr(host, "apk_autoremovable", None)
        if apk_autoremovable is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy
            # the list so callers never mutate simulated state.
            return list(apk_autoremovable)
        # Real transport: simulate the orphan purge over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


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
    audit is after. Which of those apk's solver would actually drop -- the
    ones nothing in the world reaches any more -- is what the sibling
    ``ApkAutoremovable`` reports, and cleaning them up is what the
    ``apk.autoremove`` operation does; this fact only lists the world, so
    cleanup stays an explicit operator decision, never a side effect of
    reading the world. Each line's constraint is
    deliberately dropped, exactly as ``AptAutoremovable`` drops the version
    its simulation rows carry: the ``@tag`` a package may be selected from
    and the ``=``, ``<``, ``>``, ``>=``, ``~`` or ``><`` version bound it is
    pinned to say nothing about *which* packages were asked for. The version
    bound a *hold* is after -- the constraint this fact drops -- is exactly
    what the sibling ``ApkHeldPackages`` keeps. A leading
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


class ApkHeldPackages:
    """The held (version-pinned) apk packages, as a dict of name -> constraint.

    The apk counterpart of ``apt.AptHeldPackages``: apk has no ``apt-mark
    hold`` -- it keeps a package out of upgrades by pinning a *version
    constraint* into its world, ``/etc/apk/world``, so a held package is
    exactly a world line carrying a version bound. Each such line becomes
    one entry mapping the bare package name to its constraint (the operator
    and version kept together), for example::

        {"curl": "=8.14.0-r0", "openssh": ">~9.7_p1-r4"}

    The shape deliberately differs from the sibling ``AptHeldPackages``'
    sorted name list: ``apt-mark hold`` records a bare name with no version,
    where an apk pin's whole purpose is the constraint, so dropping it would
    make a re-pin to a different version undetectable. It reads the same
    ``/etc/apk/world`` file as ``ApkExplicitPackages`` -- which reports the
    bare names and deliberately drops every constraint -- and keeps only the
    lines ``_VERSION_OP_REGEX`` finds a version operator in, so a bare name
    (``busybox``) and a repository-tag-only line (``nginx@edge``, a selection
    not a hold) never contribute. Comments (``#``), blank lines and a leading
    ``!`` conflict constraint are skipped, and a line whose name is empty is
    ignored, exactly as ``ApkExplicitPackages`` skips them; entries keep file
    order (a repeated name's last line wins) since the dict answers a
    name-keyed lookup, not a listing. A missing or unreadable file -- any
    host without apk -- reads as ``{}`` through the command's ``|| true``.
    The ``apk.hold`` operation reads this fact for version-aware idempotence
    and writes it back through ``apk add <pkg><constraint>``.
    """

    #: The file ``apk`` keeps its world -- the explicitly installed package
    #: constraints -- in: one ``[!]name{@tag}{[<>~=]version}`` per line. The
    #: same file ``ApkExplicitPackages`` reads for the bare names; a held
    #: package is a line here carrying a version bound.
    PATH = "/etc/apk/world"

    #: Cat the world file, tolerating its absence: the ``|| true`` keeps a
    #: missing file (a non-Alpine host, or ``cat``'s error on stderr) from
    #: failing the gather, so the fact reads as ``{}`` -- the same command
    #: shape ``ApkExplicitPackages`` builds for the same file.
    COMMAND = f"cat -- {PATH} 2>/dev/null || true"

    #: The characters that start a world constraint's repository tag or
    #: version bound: the first of them ends the bare package name, so
    #: everything before it is the name the dict keys on (the ``@tag`` a
    #: pinned line may also carry is dropped from the key, exactly as
    #: ``ApkExplicitPackages`` drops it).
    BOUNDARY_REGEX = re.compile(r"[@<>~=]")

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, str]:
        """Parse ``/etc/apk/world`` lines into name -> version constraint.

        Comments (``#``), blank lines and a ``!`` conflict constraint are
        skipped. Each remaining line contributes an entry only when it
        carries a version bound -- ``_split_constraint`` finds the operator
        -- so a bare name and a repository-tag-only line (``nginx@edge``)
        are not holds and never appear. The key is the bare name (the text
        up to the first tag or version boundary), the value the constraint
        from that operator on (``=8.14.0-r0``). Entries keep file order; a
        repeated name's last line wins. Pure and testable.
        """
        held: dict[str, str] = {}
        for raw in output:
            line = raw.strip()
            if not line or line.startswith(("#", "!")):
                continue
            name = cls.BOUNDARY_REGEX.split(line, maxsplit=1)[0].strip()
            if not name:
                continue
            _, constraint = _split_constraint(line)
            if constraint:
                held[name] = constraint
        return held

    @classmethod
    async def process(cls, host: Host) -> dict[str, str]:
        apk_held_packages = getattr(host, "apk_held_packages", None)
        if apk_held_packages is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy
            # the dict so callers never mutate simulated state.
            return dict(apk_held_packages)
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
    the rows either: ``ApkExplicitPackages`` reports the world names, and
    the orphans apk's solver would drop -- the installed names nothing in
    the world reaches any more -- are what ``ApkAutoremovable`` reports.
    The rows give no single package's dependencies, size or description
    either: ``ApkPackage`` queries one package's detail directly.
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


# --- Operations --------------------------------------------------------------


def _split_version(package: str) -> tuple[str, str | None]:
    """Split ``pkg=version`` into ``(name, version)``; version is None if absent.

    pyinfra's ``PkgInfo.from_possible_pair`` splits on the *last* ``=``
    (``rsplit``), so a version string carrying ``=`` stays intact -- the
    same split ``apt.packages`` and ``dnf.packages`` use.
    """
    name, sep, version = package.rpartition("=")
    return (name, version) if sep else (package, None)


@dataclass(frozen=True)
class ApkInstallCommand(Command):
    """``apk add <packages>``: install (or upgrade) packages.

    The ``present=True`` direction of ``apk.packages``. apk never prompts,
    so the shell is pyinfra's bare ``apk add`` (no ``-y``) plus the
    shell-quoted package names, ``<pkg>=<version>`` pins included.
    ``no_cache`` adds apk's ``--no-cache`` flag (bypass the package cache
    entirely -- neither read from nor write to ``/var/cache/apk``), and
    ``no_scripts`` adds ``--no-scripts`` (skip post-install triggers and
    scriptlets). Both are apk-native modifiers with no apt/dnf equivalent,
    so they are a deliberate asymmetry on the shared package-manager
    surface; they affect only how the install runs, never what the
    ``ApkPackages`` fact reports, so idempotence is unchanged.
    """

    packages: tuple[str, ...]
    no_cache: bool = False
    no_scripts: bool = False

    def shell(self) -> str:
        command = "add"
        if self.no_cache:
            command += " --no-cache"
        if self.no_scripts:
            command += " --no-scripts"
        names = " ".join(shlex.quote(package) for package in self.packages)
        return f"apk {command} {names}"


@dataclass(frozen=True)
class ApkRemoveCommand(Command):
    """``apk del <packages>``: remove packages.

    The ``present=False`` direction of ``apk.packages`` (pyinfra's bare
    ``apk del`` uninstall command); names are shell-quoted, ``<pkg>=<version>``
    pins included.
    """

    packages: tuple[str, ...]

    def shell(self) -> str:
        names = " ".join(shlex.quote(package) for package in self.packages)
        return f"apk del {names}"


@dataclass(frozen=True)
class ApkUpgradeCommand(Command):
    """``apk upgrade <packages>``: upgrade the named packages.

    The ``latest`` upgrade path of ``apk.packages`` (pyinfra's
    ``upgrade_command``); names are shell-quoted. ``no_cache`` and
    ``no_scripts`` behave exactly as on ``ApkInstallCommand`` -- apk's
    ``upgrade`` accepts the same flags -- so the ``latest`` path honours
    the same install modifiers the ``present=True`` path does.
    """

    packages: tuple[str, ...]
    no_cache: bool = False
    no_scripts: bool = False

    def shell(self) -> str:
        command = "upgrade"
        if self.no_cache:
            command += " --no-cache"
        if self.no_scripts:
            command += " --no-scripts"
        names = " ".join(shlex.quote(package) for package in self.packages)
        return f"apk {command} {names}"


async def packages(
    ctx: OpContext,
    packages: str | list[str] | tuple[str, ...] | None = None,
    present: bool = True,
    latest: bool = False,
    no_cache: bool = False,
    no_scripts: bool = False,
    **kwargs,
) -> AsyncGenerator[Command]:
    """Add/remove/update apk packages (pyinfra ``apk.packages`` parity).

    + packages: packages to ensure; a single string, a list/tuple, or None.
      Prefer tuples when building requests via ``op()``, since
      ``OperationRequest`` kwargs must stay hashable. Versions can be
      pinned as ``<pkg>=<version>``.
    + present: whether the packages should be installed
    + latest: whether to upgrade installed packages without a pinned version
    + no_cache: add ``--no-cache`` to the install/upgrade command, bypassing
      the package cache entirely (neither read from nor write to
      ``/var/cache/apk``)
    + no_scripts: add ``--no-scripts`` to the install/upgrade command,
      skipping post-install triggers and scriptlets

    The ``ApkPackages`` fact supplies the installed versions, so a pinned
    ``<pkg>=<version>`` is present only when that *exact* version is
    installed (pyinfra's ``ensure_packages`` version match, the same rule
    ``apt.packages`` follows), and ``latest`` follows pyinfra's legacy
    behavior: every installed, unpinned package is upgraded with
    ``apk upgrade`` without checking whether an upgrade actually exists.
    Installing, removing and upgrading all need root, so flag the request
    ``sudo=True`` (or ``su=True``). ``**kwargs`` absorbs pyinfra's global
    arguments (``op()`` already strips ``name``/``sudo``/``su``).

    ``no_cache`` and ``no_scripts`` are apk-native modifiers with no
    apt/dnf equivalent -- apt's ``no_recommends``/``allow_downgrades`` and
    dnf's ``nobest`` are the closest cousins, but neither tool exposes a
    cache-bypass or script-skip flag on its package operation -- so they
    are a deliberate apk-only asymmetry on the shared package-manager
    surface. They affect only how the install/upgrade runs, never what the
    ``ApkPackages`` fact reports, so idempotence is unchanged and the
    removal direction (``present=False``) ignores both.

    Deliberate deviations from pyinfra: the ``update`` and ``upgrade``
    options -- which prefix the operation with a whole-system ``apk update``
    / ``apk upgrade`` -- are deliberately dropped, so those keywords are
    absorbed by ``**kwargs`` with no effect; reemote5 keeps the
    whole-system refresh/upgrade out of the package operation.
    """
    reasons: list[str] = []

    # Normalize: pyinfra accepts a bare string, a list, or None.
    if packages is None:
        requested: tuple[str, ...] = ()
    elif isinstance(packages, str):
        requested = (packages,)
    else:
        requested = tuple(packages)

    if requested:
        installed = await ctx.facts.get(ApkPackages)

        def is_installed(package: str) -> bool:
            name, version = _split_version(package)
            versions = installed.get(name)
            if versions is None:
                return False
            return version is None or version in versions

        if present:
            to_install = tuple(pkg for pkg in requested if not is_installed(pkg))
            # ``latest``: installed, unpinned packages are upgrade candidates.
            to_upgrade = (
                tuple(
                    pkg
                    for pkg in requested
                    if _split_version(pkg)[1] is None and is_installed(pkg)
                )
                if latest
                else ()
            )
            if to_install:
                ctx.changed = True
                reasons.append(f"{len(to_install)} package(s) to install")
                yield ApkInstallCommand(
                    packages=to_install,
                    no_cache=no_cache,
                    no_scripts=no_scripts,
                )
            if to_upgrade:
                ctx.changed = True
                reasons.append(f"{len(to_upgrade)} package(s) to upgrade")
                yield ApkUpgradeCommand(
                    packages=to_upgrade,
                    no_cache=no_cache,
                    no_scripts=no_scripts,
                )
            if not to_install and not to_upgrade:
                reasons.append("all packages present")
        else:
            to_remove = tuple(pkg for pkg in requested if is_installed(pkg))
            if to_remove:
                ctx.changed = True
                reasons.append(f"{len(to_remove)} package(s) to remove")
                yield ApkRemoveCommand(packages=to_remove)
            else:
                reasons.append("no packages to remove")
    else:
        reasons.append("no packages requested")

    ctx.reason = "; ".join(reasons)


# --- apk.virtual -------------------------------------------------------------


@dataclass(frozen=True)
class ApkVirtualCommand(Command):
    """``apk add -t <tag> <packages>`` / ``apk del <tag>``: manage a virtual group.

    The single present-flagged descriptor ``apk.virtual`` yields, following the
    ``server.ModprobeCommand``/``files.FileCommand`` precedent -- both
    directions are flags of the same base tool (``apk``), so one type covers
    them. ``present=True`` creates the virtual package ``tag`` depending on
    ``packages`` with apk's ``-t``/``--virtual`` option (``apk add -t <tag>
    <packages>``); ``present=False`` removes the whole group with ``apk del
    <tag>``, dropping ``tag`` and the members nothing else needs. The tag and
    the package names are shell-quoted. ``packages`` is unused on the remove
    direction (apk names only the group) but stays a field so both directions
    are one descriptor type.
    """

    tag: str
    packages: tuple[str, ...] = ()
    present: bool = True

    def shell(self) -> str:
        tag = shlex.quote(self.tag)
        if not self.present:
            return f"apk del {tag}"
        names = " ".join(shlex.quote(package) for package in self.packages)
        return f"apk add -t {tag} {names}" if names else f"apk add -t {tag}"


async def virtual(
    ctx: OpContext,
    tag: str,
    packages: str | list[str] | tuple[str, ...] | None = None,
    present: bool = True,
    **kwargs,
) -> AsyncGenerator[Command]:
    """Manage an apk virtual package group (reemote5 original).

    + tag: the virtual package name apk records in ``/etc/apk/world`` -- the
      group's identity, created with ``apk add -t <tag>`` and removed with
      ``apk del <tag>``.
    + packages: the member packages the virtual group depends on; a single
      string, a list/tuple, or None. Prefer tuples when building requests via
      ``op()``, since ``OperationRequest`` kwargs must stay hashable. Versions
      can be pinned as ``<pkg>=<version>``.
    + present: whether the virtual group should exist.

    A reemote5 original: pyinfra's ``apk`` namespace ships only ``upgrade``,
    ``update`` and ``packages`` -- no virtual-group management -- so this
    operation has no upstream counterpart. Alpine's ``apk add -t <tag>
    <packages>`` (``--virtual``) creates a temporary virtual package ``tag``
    that depends on ``packages``, records ``tag`` in ``/etc/apk/world``, and
    lets the whole group be torn down later with a single ``apk del <tag>`` --
    the build-dependency pattern a compile step wraps itself in so its
    toolchain does not linger on the finished image.

    Idempotence reads ``ApkExplicitPackages`` (the world names): the group is
    present exactly when ``tag`` appears there. ``present=True`` creates it with
    ``apk add -t`` only when ``tag`` is absent (and only when at least one
    member package is requested); ``present=False`` removes it with ``apk del``
    only when ``tag`` is present. A deliberate simplification: the fact reports
    the world *names* only, so a changed member set for an already-existing
    ``tag`` is NOT detected -- the group reads as present and the operation is a
    no-op -- because apk records a virtual group's identity in world, not its
    membership, and comparing members would need the fuzzy version-constrained
    ``depends`` strings ``ApkPackage`` reports. Creating and removing both write
    the root-owned apk database and world file, so flag the request ``sudo=True``
    (or ``su=True``). ``**kwargs`` absorbs pyinfra's global arguments (``op()``
    already strips ``name``/``sudo``/``su``).
    """
    reasons: list[str] = []

    # Normalize: pyinfra-style operations accept a bare string, a list, or None.
    if packages is None:
        requested: tuple[str, ...] = ()
    elif isinstance(packages, str):
        requested = (packages,)
    else:
        requested = tuple(packages)

    world = await ctx.facts.get(ApkExplicitPackages)
    exists = tag in world

    if present:
        if not requested:
            reasons.append(f"no packages requested for virtual package {tag}")
        elif exists:
            reasons.append(f"virtual package {tag} already present")
        else:
            ctx.changed = True
            reasons.append(
                f"virtual package {tag} will be created with {len(requested)} package(s)"
            )
            yield ApkVirtualCommand(tag=tag, packages=requested, present=True)
    else:
        if exists:
            ctx.changed = True
            reasons.append(f"virtual package {tag} will be removed")
            yield ApkVirtualCommand(tag=tag, packages=requested, present=False)
        else:
            reasons.append(f"virtual package {tag} not present")

    ctx.reason = "; ".join(reasons)


# --- apk.hold ----------------------------------------------------------------


@dataclass(frozen=True)
class ApkHoldCommand(Command):
    """``apk add <packages>``: pin (or release) a package's version hold.

    The single present-flagged descriptor ``apk.hold`` yields, following the
    ``ApkVirtualCommand``/``server.ModprobeCommand`` precedent -- both
    directions are the same base verb (``apk add``), so one type covers them.
    apk has no ``apt-mark``-style hold verb: it keeps a package out of
    upgrades by writing a version constraint into ``/etc/apk/world``, and
    ``apk add <pkg><constraint>`` is how that constraint is set. So
    ``present=True`` re-adds each ``packages`` entry *with* its constraint
    (``apk add curl=8.14.0-r0``), pinning it, while ``present=False``
    re-adds each bare name (``apk add curl``), which rewrites the world line
    without a bound and releases the pin -- the package stays installed, only
    the hold goes. The names (constraints included) are shell-quoted.
    """

    packages: tuple[str, ...]
    present: bool = True

    def shell(self) -> str:
        names = " ".join(shlex.quote(package) for package in self.packages)
        return f"apk add {names}"


async def hold(
    ctx: OpContext,
    packages: str | list[str] | tuple[str, ...],
    present: bool = True,
    version: str | None = None,
    **kwargs,
) -> AsyncGenerator[Command]:
    """Hold packages back from upgrades, or release held ones (apk pins).

    + packages: package names to hold or unhold; a single string or a
      list/tuple. Prefer tuples when building requests via ``op()``, since
      ``OperationRequest`` kwargs must stay hashable.
    + present: whether the hold should be present (``True``, pin the package)
      or removed (``False``, release the pin)
    + version: the optional version constraint to pin to -- a bare
      ``1.2.3-r0`` (normalized to ``=1.2.3-r0``) or an operator-led
      ``~1.2``/``<2.0``. When omitted with ``present=True``, each package is
      pinned to its currently installed version (``ApkPackages``' newest);
      ignored on the ``present=False`` direction.

    The apk counterpart of ``apt.mark``: pyinfra ships no apk hold, and apk
    has no ``apt-mark`` verb -- it keeps a package out of upgrades by pinning
    a version constraint into ``/etc/apk/world``. Idempotent on
    ``ApkHeldPackages`` (name -> constraint): ``present=True`` yields one
    ``ApkHoldCommand`` for only the requested names whose current pin differs
    from the wanted constraint (a name already pinned to it is a no-op), and
    ``present=False`` one for only the names currently held. The command keeps
    the requested order (stable across phases).

    A deliberate consequence of pinning to the installed version: with
    ``version`` omitted, a requested name that is *not* installed cannot be
    pinned (there is no version to pin to) and is reported but not held --
    pass ``version`` to pin a package that is not yet installed. Holding
    writes the root-owned apk database and world file, so flag the request
    ``sudo=True`` (or ``su=True``). ``**kwargs`` absorbs pyinfra's global
    arguments (``op()`` already strips ``name``/``sudo``/``su``).
    """
    # Normalize like apt.mark: a bare string is one name.
    requested = (packages,) if isinstance(packages, str) else tuple(packages)

    if not requested:
        ctx.reason = "no packages requested"
        return

    current = await ctx.facts.get(ApkHeldPackages)
    reasons: list[str] = []

    if present:
        to_hold: list[str] = []
        unpinnable: list[str] = []
        if version is not None:
            # An explicit constraint pins every requested name the same way;
            # no installed-version lookup is needed.
            pin = _normalize_constraint(version)
            for name in requested:
                if current.get(name) != pin:
                    to_hold.append(f"{name}{pin}")
        else:
            # Pin to each name's currently installed version -- read the fact
            # only on this branch (``version`` is a fixed kwarg, so the gather
            # set is identical in both phases).
            installed = await ctx.facts.get(ApkPackages)
            for name in requested:
                versions = installed.get(name)
                if not versions:
                    unpinnable.append(name)
                    continue
                pin = f"={versions[-1]}"
                if current.get(name) != pin:
                    to_hold.append(f"{name}{pin}")
        if to_hold:
            ctx.changed = True
            reasons.append(f"{len(to_hold)} package(s) to hold")
            yield ApkHoldCommand(packages=tuple(to_hold), present=True)
        elif not unpinnable:
            reasons.append("all packages held")
        if unpinnable:
            reasons.append(
                f"{len(unpinnable)} package(s) not installed and no version given"
            )
    else:
        to_unhold = tuple(name for name in requested if name in current)
        if to_unhold:
            ctx.changed = True
            reasons.append(f"{len(to_unhold)} package(s) to unhold")
            yield ApkHoldCommand(packages=to_unhold, present=False)
        else:
            reasons.append("no held packages to unhold")

    ctx.reason = "; ".join(reasons)


# --- apk.autoremove ----------------------------------------------------------


@dataclass(frozen=True)
class ApkPurgeCommand(Command):
    """``apk add --purge``: remove packages nothing in the world reaches.

    The mutation ``apk.autoremove`` yields when ``ApkAutoremovable`` names
    purgable packages. apk has no ``autoremove`` subcommand: ``apk add
    --purge`` with no package arguments runs the solver and drops every
    installed package no world constraint (directly or transitively) needs
    any more, which is exactly the cleanup this operation performs. The
    shell is deliberately minimal -- no packages, no other flags -- so it
    never installs or upgrades anything on the way, matching the
    single-purpose ``apt-get autoremove`` shape ``AptAutoRemoveCommand``
    renders.
    """

    def shell(self) -> str:
        return "apk add --purge"


async def autoremove(ctx: OpContext, **kwargs) -> AsyncGenerator[Command]:
    """Remove orphan packages nothing in the world reaches any more.

    The apk counterpart of ``apt.auto_remove`` and a reemote5 original:
    pyinfra's ``apk`` namespace ships no cleanup verb, and apk itself has no
    ``autoremove`` subcommand -- but it does have the ``--purge`` flag of
    ``apk add``, which runs the solver and removes every installed package
    no ``/etc/apk/world`` constraint (directly or transitively) needs any
    more. The ``ApkAutoremovable`` fact simulates exactly that plan and
    names the orphans: a non-empty list yields the ``ApkPurgeCommand`` (the
    names are already sorted, so the reason reads identically in both
    phases), an empty list is a no-op. Purging needs root, so flag the
    request ``sudo=True`` (or ``su=True``). ``**kwargs`` absorbs pyinfra's
    global arguments (``op()`` already strips ``name``/``sudo``/``su``).

    Deliberate deviation from ``apt.auto_remove``: no ``wait_for_lock``
    parameter, because apk uses no lock file the way apt's ``DPkg::Lock``
    does -- concurrent apk invocations simply fail with a database-busy
    error rather than waiting. A caller needing serialisation sequences the
    operation behind its own guard.
    """
    removable = await ctx.facts.get(ApkAutoremovable)
    if removable:
        ctx.changed = True
        ctx.reason = f"purgable packages will be removed: {', '.join(removable)}"
        yield ApkPurgeCommand()
    else:
        ctx.reason = "no purgable packages"


__all__ = [
    "ApkAutoremovable",
    "ApkExplicitPackages",
    "ApkHeldPackages",
    "ApkHoldCommand",
    "ApkInstallCommand",
    "ApkPackage",
    "ApkPackageInfo",
    "ApkPackages",
    "ApkPurgeCommand",
    "ApkRemoveCommand",
    "ApkRepositories",
    "ApkUpgradeCommand",
    "ApkVirtualCommand",
    "autoremove",
    "hold",
    "packages",
    "virtual",
]
