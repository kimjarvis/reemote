# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# The ``apk`` module: facts in one source.
#
# Fact side: every package-list fact reports ``ApkPackageVersion`` entries
# (name plus version): ``ApkInstalledPackages``, the installed Alpine Linux
# (apk) packages, one entry per installed name-and-version parsed from
# ``apk list --installed`` rows,
# ``ApkExplicitPackages``, the explicitly installed (world) packages parsed
# from ``/etc/apk/world`` with the line's version constraint as the version,
# ``ApkHeldPackages``, the version-pinned (held) packages parsed from the same
# ``/etc/apk/world`` with the constraint (operator included) as the version,
# ``ApkAutoremovablePackages``, the orphan packages ``apk add --purge`` would
# remove, parsed from its ``--simulate`` rows. ``ApkRepositories``, the
# configured apk repositories as ``ApkRepository`` entries parsed from
# ``/etc/apk/repositories``, and ``ApkPackage``, one
# package's detailed metadata (version, description, size, dependencies,
# provides) parsed from the labelled sections of ``apk info -a``. Like every
# fact they are phase-unaware: their ``process`` coroutine just gathers the
# raw value.
#
# Operation side: ``apk.packages`` plus the typed command descriptors it
# yields (``ApkInstallCommand``/``ApkRemoveCommand``/``ApkUpgradeCommand``).
# ``apk.packages`` mirrors pyinfra's ``apk.packages`` (add/remove packages
# with version pins and ``latest`` upgrades), reading ``ApkInstalledPackages``, and
# exposes two apk-native install modifiers -- ``no_cache``
# (``apk add --no-cache``, bypass the package cache) and ``no_scripts``
# (``apk add --no-scripts``, skip post-install triggers) -- which apt and
# dnf have no equivalent for, so they are a deliberate apk-only asymmetry
# on the shared package-manager surface;
# pyinfra's ``update`` and ``upgrade`` prefix options are deliberately
# dropped -- reemote5 keeps the whole-system ``apk update``/``apk upgrade``
# out of the package operation, and both keywords are absorbed by
# ``**kwargs`` with no effect. The remaining pyinfra ``apk`` operations
# (``apk.upgrade``, ``apk.key``, ``apk.repo``) are not migrated.
# ``apk.virtual`` -- a reemote5 original with no pyinfra counterpart --
# manages a *virtual package* group (``apk add -t <tag>`` to create it,
# ``apk del <tag>`` to remove it) through its present-flagged
# ``ApkVirtualCommand``, reading ``ApkExplicitPackages``' entry names for
# world idempotence. ``apk.hold`` -- the apk counterpart of ``apt.mark`` -- pins
# packages back from upgrades by writing a version constraint into
# ``/etc/apk/world`` (``apk add <pkg>=<version>`` to hold, ``apk add <pkg>``
# to release the pin) through its present-flagged ``ApkHoldCommand``, reading
# ``ApkHeldPackages`` for version-aware idempotence. ``apk.autoremove`` -- the
# apk counterpart of ``apt.auto_remove``, and a reemote5 original: pyinfra's
# ``apk`` namespace has no cleanup verb, and apk itself ships no
# ``autoremove`` subcommand -- purges the orphan packages nothing in
# ``/etc/apk/world`` reaches any more through its ``ApkPurgeCommand``
# (``apk add --purge``), reading ``ApkAutoremovablePackages`` for idempotence.
# Operations are async generators that receive an ``OpContext``,
# await facts, set ``ctx.changed``/``ctx.reason``, and yield command
# descriptors; they never touch the host directly.


import re
import shlex
from collections.abc import AsyncGenerator, Iterable
from dataclasses import dataclass
from typing import Any, ClassVar, TypedDict

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
    bare name and tag split the line with ``_split_world_line``.
    """
    match = _VERSION_OP_REGEX.search(package)
    if match is None:
        return package, ""
    index = match.start()
    return package[:index], package[index:]


def _split_world_line(line: str) -> tuple[str, str, str]:
    """Split an apk world line into ``(name, tag, constraint)``.

    A ``/etc/apk/world`` line is ``[!]name{@tag}{[<>~=]version}``, so the
    first ``@`` or version-operator character -- found with a regex search
    on the same character class ``ApkHeldPackages.BOUNDARY_REGEX`` matches,
    never ``str.split`` on a multi-character delimiter, which would silently
    mis-parse -- ends the bare package name; an ``@`` boundary introduces
    the repository tag -- the tagged repository the line selects, kept
    without the ``@`` -- running to the first version operator, and
    everything from the first version operator on is the constraint, its
    operator included (``=8.14.0-r0``, ``>~9.7``). A tag-only line
    (``nginx@edge``, a selection not a hold) and a bare name both read with
    an empty constraint. Pure and testable through the fact's ``parse``.
    """
    boundary = ApkHeldPackages.BOUNDARY_REGEX.search(line)
    if boundary is None:
        # no tag, no version bound: a bare world name.
        return line.strip(), "", ""
    name = line[: boundary.start()].strip()
    at_or_op = boundary.group()
    tail = line[boundary.end() :]
    if at_or_op == "@":
        # a version operator cannot itself be part of a tag, so the tag runs
        # to the first operator and the constraint keeps its operator.
        match = _VERSION_OP_REGEX.search(tail)
        if match is None:
            return name, tail, ""
        return name, tail[: match.start()], tail[match.start() :]
    return name, "", at_or_op + tail


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


class ApkPackageVersion(TypedDict):
    """
    Attributes:
        name: The bare package name, e.g. ``"curl"`` -- the ``@tag`` a world
            line may carry is not part of it.
        version: The version-and-release (``"8.14.0-r0"``) for
            ``ApkInstalledPackages`` and ``ApkAutoremovablePackages``; the
            world constraint verbatim, operator included (``"=8.14.0-r0"``,
            ``">~9.7_p1-r4"``), for the world-derived ``ApkExplicitPackages``
            and ``ApkHeldPackages``, ``""`` when a world line carries no
            version bound.
    """

    # One apk package at one version -- the shared entry of every apk
    # package-list fact: ``ApkInstalledPackages``, ``ApkAutoremovablePackages``,
    # ``ApkExplicitPackages`` and ``ApkHeldPackages`` all return
    # ``list[ApkPackageVersion]``, so one typed shape answers every
    # package-and-version question the namespace asks.
    #
    # A TypedDict rather than a class instance: entries are ordinary dicts, so
    # equality against a literal dict works and ``json.dumps`` serializes them
    # directly. The world-derived facts keep the constraint's operator inside
    # ``version`` so a re-pin to a different bound stays detectable; the
    # ``@tag`` repository selector a world line may carry is deliberately
    # dropped everywhere.

    name: str
    version: str


class ApkAutoremovablePackages:
    """Orphaned packages that purge would remove.

    **Commands:**

    ```bash
    apk add --purge --simulate 2>/dev/null
    ```

    **Returns:**

    ```python
    list[ApkPackageVersion]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apk/apk_apk_autoremovable.py``.
    """

    # The apk counterpart of ``apt.AptAutoremovable`` and a reemote5 original:
    # pyinfra's ``apk`` namespace ships no cleanup verb, and apk itself has no
    # ``autoremove`` subcommand -- but it does have a first-class orphan-purge
    # mechanism, the ``--purge`` flag of ``apk add``/``apk fix``. Running
    # ``apk add --purge`` with no packages asks apk's solver to remove every
    # installed package nothing in ``/etc/apk/world`` reaches any more, and
    # adding ``--simulate`` prints the same plan without touching the system,
    # one ``(N/M) Purging <name>-<version>`` row per orphan, collected into
    # one ``ApkPackageVersion`` dict per row, for example::
    #
    #     [
    #         {"name": "libllvm17", "version": "17.0.6-r0"},
    #         {"name": "py3-pycparser", "version": "2.22-r0"},
    #         {"name": "py3-pycparser-pyc", "version": "2.22-r0"},
    #     ]
    #
    # Only the ``Purging`` rows are read: the leading ``N packages to purge``
    # summary, the trailing ``OK: <size> in <count> packages`` footer, the
    # progress lines and blank lines are never mistaken for package rows, and
    # each row's ``name-version-release`` token splits at the same last
    # ``-<version>-r<release>`` boundary ``ApkInstalledPackages`` splits its
    # listing
    # rows at -- so multi-dash names (``alpine-baselayout-data``) and names
    # carrying punctuation (``libstdc++``) stay intact -- but keeps the
    # version-and-release this time rather than dropping it, since a TypedDict
    # entry has a field to carry it in. Nothing purgable (or no output at all)
    # reads as ``[]``, exactly as ``AptAutoremovable``' empty simulation does.
    # This fact fills a coverage gap of ``ApkInstalledPackages``: the ``apk list
    # --installed`` rows that fact parses cannot tell an orphaned dependency
    # from one still reachable from the world, so a deploy cleaning up after
    # itself reads the purgable packages here -- and only collects the list;
    # removal stays an explicit operator decision, never a fact side effect --
    # the ``apk.autoremove`` operation performs that cleanup. The simulation
    # needs no root, and a missing ``apk`` exits 127, which ``run_shell``
    # reports as ``CommandNotFoundError``.
    #
    # Two deliberate deviations from ``AptAutoremovable``: apk's ``--simulate``
    # output is human-readable rather than machine-readable, so the row format
    # is less stable than ``apt-get --just-print autoremove``'s -- the same
    # pragmatic trade-off reemote5 already accepts for apt's ``Remv`` rows,
    # the parser is strict about the shape it recognises and simply skips a row
    # whose format changed, so a future apk release reads as ``[]`` rather
    # than reporting garbage -- and, unlike the apt sibling (which drops the
    # version its rows carry and returns a bare name list), this fact keeps
    # the version alongside the name in an ``ApkPackageVersion`` entry,
    # the same ``name``/``version`` pair opening ``ApkPackageInfo``'s detail,
    # since apk's purge row always prints one.

    #: Shell simulating the orphan purge over SSH, one ``(N/M) Purging`` row
    #: per package that would go. ``--simulate`` never removes anything (the
    #: fact only collects the list), and ``2>/dev/null`` suppresses the
    #: cache/index warnings an unprivileged simulation writes to stderr --
    #: the same redirect ``AptAutoremovable``'s command builds in.
    COMMAND = "apk add --purge --simulate 2>/dev/null"

    #: A simulation purge row: the ``(N/M)`` progress prefix, the ``Purging``
    #: token, then ``name-version-release`` split at the last
    #: ``-<version>-r<release>`` boundary ``ApkInstalledPackages`` splits its
    #: rows at,
    #: into the name (group 1) and the version-and-release (group 2, held with
    #: the ``([^-]+-r[^-]+)`` group shape ``ApkInstalledPackages.APK_REGEX``
    #: uses) -- so
    #: ``alpine-baselayout-data-3.6.5-r0`` keeps its multi-dash name and
    #: ``libstdc++-13.2.1_git20240309-r0`` its punctuation intact.
    PURGE_REGEX = re.compile(r"^\(\d+/\d+\) Purging (.+)-([^-]+-r[^-]+)$")

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[ApkPackageVersion]:
        """Parse ``apk add --purge --simulate`` rows into sorted, unique entries.

        Only lines matching ``PURGE_REGEX`` contribute one
        ``ApkPackageVersion`` dict naming their package and its
        version-and-release; every other line -- the summary count, the
        ``OK:`` footer, progress output, blank lines -- is skipped. A name
        repeated across rows collapses into the one entry seen last (apk's
        solver never prints two versions of one name in a single purge plan,
        so this only ever re-records an identical entry) and the result is
        sorted by name. Pure and testable.
        """
        by_name: dict[str, str] = {}
        for line in output:
            matches = cls.PURGE_REGEX.match(line.strip())
            if matches:
                by_name[matches.group(1)] = matches.group(2)
        return [{"name": name, "version": by_name[name]} for name in sorted(by_name)]

    @classmethod
    async def process(cls, host: Host) -> list[ApkPackageVersion]:
        apk_autoremovable = getattr(host, "apk_autoremovable", None)
        if apk_autoremovable is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy
            # each dict so callers never mutate simulated state.
            return [dict(entry) for entry in apk_autoremovable]
        # Real transport: simulate the orphan purge over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class ApkExplicitPackages:
    """The explicitly installed apk packages.

    **Commands:**

    ```bash
    cat -- /etc/apk/world 2>/dev/null || true
    ```

    **Returns:**

    ```python
    list[ApkPackageVersion]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apk/apk_apk_explicit_packages.py``.
    """

    # A reemote5 original: pyinfra's ``apk`` namespace ships only
    # ``ApkPackages``, so nothing upstream can separate the packages an
    # operator asked for from the ones apk pulled in as dependencies. apk
    # records the former -- its *world* -- in ``/etc/apk/world``, one
    # dependency constraint per line in ``[!]name{@tag}{[<>~=]version}``
    # notation, and each line becomes one ``ApkPackageVersion`` entry: the
    # bare package name plus the line's version constraint verbatim as the
    # ``version`` (``""`` for a bare name), for example::
    #
    #     [
    #         {"name": "busybox", "version": ""},
    #         {"name": "curl", "version": "=8.14.0-r0"},
    #         {"name": "openssh", "version": ""},
    #     ]
    #
    # Entries are de-duplicated and sorted by name, then version, each a
    # TypedDict-shaped dict like every sibling package-list fact reports --
    # the same ``ApkPackageVersion`` shape ``apk.ApkAutoremovablePackages``
    # and ``apk.ApkInstalledPackages`` return --
    # so the entries' names compare directly against ``ApkInstalledPackages``'
    # entry names: the
    # installed names missing from this list are the dependency-only ones an
    # audit is after. Which of those apk's solver would actually drop -- the
    # ones nothing in the world reaches any more -- is what the sibling
    # ``ApkAutoremovablePackages`` reports, and cleaning them up is what the
    # ``apk.autoremove`` operation does; this fact only lists the world, so
    # cleanup stays an explicit operator decision, never a side effect of
    # reading the world. The ``@tag`` a package may be selected from is
    # deliberately dropped -- a tagged repository says nothing about *which*
    # packages were asked for -- while the ``=``, ``<``, ``>``, ``>=``, ``~``
    # or ``><`` version bound a line pins to is kept verbatim in ``version``,
    # operator included, so a changed constraint stays visible; the sibling
    # ``ApkHeldPackages`` reports exactly the constrained subset of these
    # lines. A leading
    # ``!`` marks a conflict constraint -- a package that must NOT be
    # installed -- so it never contributes an entry. Blank lines and ``#``
    # comments are skipped and a line carrying no name (a lone operator or
    # tag) is ignored, the way the siblings skip unparseable rows. A missing
    # or unreadable file -- any host without apk -- reads as ``[]`` through
    # the command's ``|| true``, like ``ApkRepositories`` and
    # ``DnfRepositories`` tolerate their missing files. The name follows the
    # sibling facts of the package-list family rather than the file it reads
    # (``ApkWorld`` would name a file, not answer a question) and keeps
    # apk's own vocabulary -- apk documents these as the *explicitly*
    # installed packages, where apt calls them ``manual`` and dnf
    # ``userinstalled``.

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
    #: tag (dropped) or the constraint the entry keeps as its ``version``.
    BOUNDARY_REGEX = re.compile(r"[@<>~=]")

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[ApkPackageVersion]:
        """Parse ``/etc/apk/world`` lines into sorted, unique entries.

        Comments (``#``) and blank lines are skipped; so is a conflict
        constraint (a line starting with ``!``), which names a package that
        must not be installed. Each remaining line contributes an
        ``ApkPackageVersion`` whose ``name`` is the bare package name (the
        text up to the first tag or version-boundary character, stripped)
        and whose ``version`` is the line's version constraint verbatim,
        operator included -- ``""`` when the line carries none; the ``@tag``
        selector is dropped. A line whose name is empty is not a package
        constraint and is ignored. Duplicate entries collapse into one and
        the result is sorted by name, then version. Pure and testable.
        """
        entries: set[tuple[str, str]] = set()
        for raw in output:
            line = raw.strip()
            if not line or line.startswith(("#", "!")):
                continue
            name, _tag, constraint = _split_world_line(line)
            if name:
                entries.add((name, constraint))
        return [
            {"name": name, "version": version} for name, version in sorted(entries)
        ]

    @classmethod
    async def process(cls, host: Host) -> list[ApkPackageVersion]:
        apk_explicit_packages = getattr(host, "apk_explicit_packages", None)
        if apk_explicit_packages is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy
            # each dict so callers never mutate simulated state.
            return [dict(entry) for entry in apk_explicit_packages]
        # Real transport: cat the world file over asyncssh and parse.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class ApkHeldPackages:
    """The version-pinned apk packages.

    **Commands:**

    ```bash
    cat -- /etc/apk/world 2>/dev/null || true
    ```

    **Returns:**

    ```python
    list[ApkPackageVersion]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apk/apk_hold.py``.
    """

    # The apk counterpart of ``apt.AptHeldPackages``: a list of
    # ``ApkPackageVersion`` entries in world-file order.
    #
    # apk has no ``apt-mark hold`` -- it keeps a package out of upgrades by
    # pinning a *version constraint* into its world, ``/etc/apk/world``, so a
    # held package is exactly a world line carrying a version bound. Each such
    # line becomes one ``ApkPackageVersion`` entry: the bare name plus the
    # constraint (the operator and version kept together) as the ``version``,
    # collected into a list that keeps file order, for example::
    #
    #     [
    #         {"name": "curl", "version": "=8.14.0-r0"},
    #         {"name": "openssh", "version": ">~9.7_p1-r4"},
    #     ]
    #
    # The shape deliberately differs from the sibling ``AptHeldPackages``'
    # sorted name list: ``apt-mark hold`` records a bare name with no version,
    # where an apk pin's whole purpose is the constraint, so dropping it would
    # make a re-pin to a different version undetectable -- the operator stays
    # inside ``version`` for exactly that reason. It reads the same
    # ``/etc/apk/world`` file as ``ApkExplicitPackages`` -- which reports
    # every world line, unconstrained ones with an empty ``version`` -- and
    # keeps only the
    # lines ``_VERSION_OP_REGEX`` finds a version operator in, so a bare name
    # (``busybox``) and a repository-tag-only line (``nginx@edge``, a selection
    # not a hold) never contribute. Comments (``#``), blank lines and a leading
    # ``!`` conflict constraint are skipped, and a line whose name is empty is
    # ignored, exactly as ``ApkExplicitPackages`` skips them; entries keep file
    # order (a repeated name's last line wins) in one entry per name. A
    # missing or unreadable file -- any host without apk -- reads as ``[]``
    # through the command's ``|| true``.
    # Like every package-list sibling in this namespace the entries are
    # ``ApkPackageVersion`` TypedDict-shaped dicts rather than bare
    # strings -- the same usability choice ``apt.AptSources`` made over
    # pyinfra's collapse convention. The ``@tag`` repository selector a line
    # may carry is deliberately dropped: a tagged repository is a selection,
    # not part of the version bound. The ``apk.hold``
    # operation reads this fact for version-aware idempotence and writes it
    # back through ``apk add <pkg><constraint>``.

    #: The file ``apk`` keeps its world -- the explicitly installed package
    #: constraints -- in: one ``[!]name{@tag}{[<>~=]version}`` per line. The
    #: same file ``ApkExplicitPackages`` reads for the world entries; a held
    #: package is a line here carrying a version bound.
    PATH = "/etc/apk/world"

    #: Cat the world file, tolerating its absence: the ``|| true`` keeps a
    #: missing file (a non-Alpine host, or ``cat``'s error on stderr) from
    #: failing the gather, so the fact reads as ``[]`` -- the same command
    #: shape ``ApkExplicitPackages`` builds for the same file.
    COMMAND = f"cat -- {PATH} 2>/dev/null || true"

    #: The characters that start a world constraint's repository tag or
    #: version bound: the first of them ends the bare package name, so
    #: everything before it is the name the entry keys on; the ``@tag`` a
    #: pinned line may also carry is dropped
    #: (``_split_world_line`` splits the three parts of the line).
    BOUNDARY_REGEX = re.compile(r"[@<>~=]")

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[ApkPackageVersion]:
        """Parse ``/etc/apk/world`` lines into held-package entries.

        Comments (``#``), blank lines and a ``!`` conflict constraint are
        skipped. Each remaining line contributes an entry only when it
        carries a version bound -- ``_split_world_line`` reads the name, tag
        and constraint and the entry is kept when the constraint is
        non-empty -- so a bare name and a repository-tag-only line
        (``nginx@edge``) are not holds and never appear. The entry's ``name``
        is the bare name (the text up to the first tag or version boundary)
        and its ``version`` the constraint from its operator on
        (``=8.14.0-r0``); the ``@tag`` selector is dropped. Entries keep file
        order; a repeated name keeps the
        last line's constraint at its first position. Pure and testable.
        """
        held: dict[str, ApkPackageVersion] = {}
        for raw in output:
            line = raw.strip()
            if not line or line.startswith(("#", "!")):
                continue
            name, _tag, constraint = _split_world_line(line)
            if name and constraint:
                held[name] = {"name": name, "version": constraint}
        return list(held.values())

    @classmethod
    async def process(cls, host: Host) -> list[ApkPackageVersion]:
        apk_held_packages = getattr(host, "apk_held_packages", None)
        if apk_held_packages is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy
            # each entry so callers never mutate simulated state.
            return [dict(entry) for entry in apk_held_packages]
        # Real transport: cat the world file over asyncssh and parse.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class ApkInstalledPackages:
    """The installed apk packages.

    **Commands:**

    ```bash
    apk list --installed
    ```

    **Returns:**

    ```python
    list[ApkPackageVersion]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apk/apk_apk_packages.py``.
    """

    # Ported from pyinfra's ``apk.ApkPackages`` -- the same
    # ``apk list --installed`` command and the same row regex, but reported in
    # reemote5's own shape: a list of ``ApkPackageVersion`` entries rather
    # than pyinfra's name-keyed dict of version lists. The
    # ``name-version arch {origin} (license) [installed]`` rows are matched
    # with pyinfra's Superuser-derived pattern, which keeps version and release
    # inside one group, so multi-dash names (``alpine-baselayout-data``) split
    # from their ``3.6.5-r0``-style versions at the last ``-r<release>``
    # boundary, names carrying punctuation (``libstdc++``, ``p11-kit``,
    # ``json-c``) stay intact, and the ``WARNING: Ignoring APKINDEX...: No such
    # file or directory`` index-noise lines never match. Each installed
    # name-and-version pair becomes one entry -- apk lists a name once per
    # installed version, so a name installed at two versions contributes two
    # entries -- de-duplicated and sorted by name, then version (pyinfra's
    # ``parse_packages`` de-dups into a set per name), so the read is
    # deterministic and ``json.dumps``-printable, for example::
    #
    #     [
    #         {"name": "apk-tools", "version": "2.10.4-r2"},
    #         {"name": "musl", "version": "1.1.22-r3"},
    #         {"name": "zlib", "version": "1.2.11-r1"},
    #     ]
    #
    # Entry-list ordering follows the rest of this namespace
    # (``ApkAutoremovablePackages``, ``ApkExplicitPackages``,
    # ``ApkHeldPackages``); ``apt.AptPackages`` reports sorted typed entries
    # too, though it groups every version under one name where apk's entries
    # carry one pair each, while ``dnf.DnfPackages`` still reports the
    # name-keyed mapping pyinfra documents, so apk is deliberately no longer
    # shape-symmetric with dnf. Nothing installed reads as ``[]`` --
    # the empty answer every sibling typed-list fact gives -- in place of
    # pyinfra's ``default = dict``. pyinfra's ``requires_command`` gating on
    # ``apk`` is deliberately dropped: a missing ``apk`` exits 127, which
    # ``run_shell`` reports as ``CommandNotFoundError``. Which of these
    # packages the operator asked for, rather than one apk pulled in as a
    # dependency, is not visible in the rows either: ``ApkExplicitPackages``
    # reports the world names, and the orphans apk's solver would drop -- the
    # installed names nothing in the world reaches any more -- are what
    # ``ApkAutoremovablePackages`` reports. The rows give no single package's
    # dependencies, size or description either: ``ApkPackage`` queries one
    # package's detail directly.

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
    def parse(cls, output: Iterable[str]) -> list[ApkPackageVersion]:
        """Parse ``apk list --installed`` rows into sorted entries.

        Each matching line contributes its name-and-version pair (pyinfra's
        ``parse_packages`` de-dup); non-matching
        lines -- the ``WARNING:`` index noise, blank lines, rows without
        the ``{origin} (license)`` tail -- are skipped. One
        ``ApkPackageVersion`` per installed name-and-version pair is
        returned, sorted by name, then version. Pure and testable.
        """
        pairs: set[tuple[str, str]] = set()
        for line in output:
            matches = re.match(cls.APK_REGEX, line)
            if matches:
                pairs.add((matches.group(1), matches.group(2)))
        return [
            {"name": name, "version": version} for name, version in sorted(pairs)
        ]

    @classmethod
    async def process(cls, host: Host) -> list[ApkPackageVersion]:
        apk_packages = getattr(host, "apk_packages", None)
        if apk_packages is not None:
            # SimulatedHost seam: the state store keeps the name -> version
            # list mapping the install/remove mutators edit, so project it
            # into the sorted one-entry-per-version list this fact reports,
            # de-duplicated exactly as ``parse`` reads the real rows. Entries
            # are rebuilt so callers never mutate simulated state.
            return [
                {"name": name, "version": version}
                for name in sorted(apk_packages)
                for version in sorted(set(apk_packages[name]))
            ]
        # Real transport: list installed packages over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class ApkPackageInfo(TypedDict):
    """
    Attributes:
        name: The package name, e.g. ``"curl"``.
        version: The version-and-release apk reports, e.g. ``"8.14.0-r0"``
            -- the same shape ``ApkInstalledPackages`` reports in its
            entries' ``version`` field.
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

    # One apk package's detailed metadata (``ApkPackage``'s entry).
    #
    # A TypedDict rather than a class instance, like ``AptPackagePolicy``:
    # entries are ordinary dicts, so equality against a literal dict works and
    # ``json.dumps`` serializes them directly.

    name: str
    version: str
    description: str
    url: str
    license: str
    size: int
    depends: list[str]
    provides: list[str]


class ApkPackage:
    """Detailed information about a specific apk package, as an ``ApkPackageInfo``.

    **Commands:**

    ```bash
    apk info -a <package> 2>/dev/null || true
    ```

    **Returns:**

    ```python
    ApkPackageInfo | None
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apk/apk_apk_package.py``.
    """

    # A reemote5 original: pyinfra's ``apk`` namespace ships only
    # ``ApkPackages``, so nothing upstream reports a single package's
    # dependencies, size or description -- the detail a version comparison or a
    # dependency check needs. ``apk info -a <package>`` prints every field apk
    # knows about the package, each as a ``<name>-<version> <field>:`` header
    # followed by its value lines and a blank line; the tracked fields become
    # one ``ApkPackageInfo`` dict, for example::
    #
    #     {
    #         "name": "curl",
    #         "version": "8.14.0-r0",
    #         "description": "URL retrieval utility and library",
    #         "url": "https://curl.se/",
    #         "license": "curl",
    #         "size": 1085898,
    #         "depends": ["so:libc.musl-x86_64.so.1", "so:libz.so.1"],
    #         "provides": ["curl-doc"],
    #     }
    #
    # The ``name`` and ``version`` come from the header token, split at the last
    # ``-r<release>`` boundary exactly as ``ApkInstalledPackages`` splits its
    # listing
    # rows, so multi-dash names (``alpine-baselayout-data``) and punctuation
    # (``libstdc++``) stay intact. apk spells two labels differently from the
    # result keys (``webpage`` -> ``url``, ``installed size`` -> ``size``,
    # ``depends on`` -> ``depends``); the remaining ``-a`` fields apk prints
    # (``contains``, ``is required by``, ``triggers``, ``install if``,
    # ``replaces``) are deliberately not tracked -- the file listing and reverse
    # dependencies belong to other questions, not to this package's own
    # metadata. A scalar field whose section is absent reads as its empty
    # default (``""`` or ``0``); ``depends`` and ``provides`` default to ``[]``.
    # An absent package -- or a host without apk -- prints no field header (the
    # command's ``2>/dev/null || true`` swallows the non-zero exit), so
    # ``process`` reports ``None``, mirroring ``apt.DebPackage`` and
    # ``dnf.RpmPackage``'s None-on-absent convention rather than pyinfra's
    # empty-dict answer. This fact fills a coverage gap of
    # ``ApkInstalledPackages``:
    # the ``apk list --installed`` rows that fact parses report only whether a
    # name is installed and at which version, never its dependencies, size or
    # description, so a deploy verifying a specific package's detail reads it
    # here. Note one deliberate gap against the sibling ``apt.AptPolicy`` (which
    # surfaces each version's repository origins): apk's ``apk info`` prints no
    # repository field at all, so this fact reports the package's own metadata
    # only -- the ``{origin}`` source package of every installed name is what
    # ``ApkInstalledPackages`` parses, and the configured repositories are what
    # ``ApkRepositories`` reports.

    #: Matches an ``apk info`` field header: the ``name-version`` token, then
    #: the field label up to the trailing colon. The version-and-release group
    #: is ``\S+-r\S+`` (apk versions carry no spaces), so it cannot swallow the
    #: spaces of a multi-word label such as ``Installed size``; the greedy name
    #: group still splits at the last ``-r<release>`` boundary, exactly as
    #: ``ApkInstalledPackages`` splits its rows. apk capitalizes most labels
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
        """
        # ``apk info -a`` prints every field apk knows about the package. The
        # ``package`` argument is ``shlex``-quoted, and the ``2>/dev/null ||
        # true`` tail swallows the non-zero exit an absent package (or a host
        # without apk) produces, so nothing reaches stdout and ``process``
        # reports ``None`` -- the ``apt.DebPackage`` tolerance rather than
        # pyinfra's ``requires_command`` gating.
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


class ApkRepository(TypedDict):
    """
    Attributes:
        url: The repository URL, e.g.
            ``"https://dl-cdn.alpinelinux.org/alpine/v3.19/main"``.
        tag: The repository tag selecting the repository -- the ``@``-prefixed
            word apk reads before the URL (``"testing"`` for a
            ``@testing <url>`` line, kept without the leading ``@``), used by
            ``apk add --repository`` and ``/etc/apk/world`` tags -- or the empty
            string when the line carries none.
    """

    # One configured apk repository (``ApkRepositories``'s entry).
    #
    # A TypedDict rather than a class instance, like ``ApkPackageVersion``:
    # entries are ordinary dicts, so equality against a
    # literal dict works and ``json.dumps`` serializes them directly -- the same
    # choice ``apt.AptSources`` made with its ``AptSource`` entries over pyinfra's
    # dataclass/dict hybrid.

    url: str
    tag: str


class ApkRepositories:
    """The configured apk repositories, as a list of ``ApkRepository`` dicts.

    **Commands:**

    ```bash
    cat -- /etc/apk/repositories 2>/dev/null || true
    ```

    **Returns:**

    ```python
    list[ApkRepository]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apk/apk_apk_repositories.py``.
    """

    #
    # A reemote5 original: pyinfra's ``apk`` namespace ships no repositories
    # fact (only ``ApkPackages``), so ``/etc/apk/repositories`` cannot be
    # audited or conditionally modified through pyinfra facts. Each
    # non-comment line names one repository -- an optional ``@tag``
    # (``@testing``-style, for a repository referenced by ``apk add --repository``
    # or ``/etc/apk/world`` tags) followed by the URL -- and becomes one
    # ``ApkRepository`` entry with the ``url`` and its ``tag`` (``""`` when
    # untagged), for example::
    #
    #     [
    #         {"url": "https://dl-cdn.alpinelinux.org/alpine/v3.19/main", "tag": ""},
    #         {"url": "https://dl-cdn.alpinelinux.org/alpine/edge/testing", "tag": "testing"},
    #     ]
    #
    # Comments (``#``) and blank lines are ignored and entries keep file
    # order -- no dedup, no sorting -- like ``apt.AptSources`` and
    # ``dnf.DnfRepositories``, the two sibling facts this one mirrors in shape
    # and command style. A line that does not carry exactly an optional tag
    # and a URL is skipped, the way the siblings ignore unparseable rows.
    # A missing or unreadable file -- any host without apk -- reads as ``[]``
    # through the command's ``|| true``, like ``DnfRepositories`` tolerates
    # missing repo files (pyinfra's ``default = list``, had this fact existed
    # upstream). The return shape deliberately differs in size, not kind,
    # from the siblings: apk's one-line format carries only a URL and an
    # optional tag, where apt entries hold distribution/components/options
    # and dnf entries hold every INI option. Like the shared
    # ``ApkPackageVersion`` entry of this namespace's package-list facts,
    # the entry is an
    # ``ApkRepository`` TypedDict rather than an anonymous dict, so the shape
    # is named and importable.

    #: The file ``apk`` reads its repository list from -- one optional
    #: ``@tag URL`` (or bare URL) per line, ``#`` for comments.
    PATH = "/etc/apk/repositories"

    #: Cat the repositories file, tolerating its absence: the ``|| true``
    #: keeps a missing file (a non-Alpine host, or ``cat``'s error on
    #: stderr) from failing the gather, so the fact reads as ``[]`` -- the
    #: same no-root-marker tolerance ``DnfRepositories``' command builds in.
    COMMAND = f"cat -- {PATH} 2>/dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[ApkRepository]:
        """Parse ``/etc/apk/repositories`` lines into ``ApkRepository`` entries.

        ``#`` comments and blank lines are skipped; a bare URL line gets
        ``tag`` ``""`` and a ``@tag URL`` line gets the tag without its
        leading ``@``; any other shape (a lone ``@tag``, several URLs on
        one line) is not a repository line and is ignored. Entries keep
        file order. Pure and testable.
        """
        repositories: list[ApkRepository] = []
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
    async def process(cls, host: Host) -> list[ApkRepository]:
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
    ``ApkInstalledPackages`` fact reports, so idempotence is unchanged.
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
        **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add/remove/update apk packages.

    Args:
        ctx (OpContext): Execution context for the operation.
        packages (str | list[str] | tuple[str, ...] | None, optional): Packages
            to ensure. Prefer tuples when building requests via ``op()``, since
            ``OperationRequest`` kwargs must stay hashable. Versions can be
            pinned as ``<pkg>=<version>``. Defaults to None.
        present (bool, optional): Whether the packages should be installed.
            Defaults to True.
        latest (bool, optional): Whether to upgrade installed packages without a
            pinned version. Defaults to False.
        no_cache (bool, optional): Add ``--no-cache`` to the install/upgrade
            command, bypassing the package cache entirely (neither read from
            nor write to ``/var/cache/apk``). Defaults to False.
        no_scripts (bool, optional): Add ``--no-scripts`` to the install/upgrade
            command, skipping post-install triggers and scriptlets.
            Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated apk package commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apk/apk_apk_packages.py``.
    """

    # The ``ApkInstalledPackages`` fact supplies the installed versions, so a
    # pinned
    # ``<pkg>=<version>`` is present only when that *exact* version is
    # installed (pyinfra's ``ensure_packages`` version match, the same rule
    # ``apt.packages`` follows), and ``latest`` follows pyinfra's legacy
    # behavior: every installed, unpinned package is upgraded with
    # ``apk upgrade`` without checking whether an upgrade actually exists.
    # Installing, removing and upgrading all need root, so flag the request
    # ``sudo=True``, ``doas=True`` or ``su=True``. Stock Alpine ships no
    # ``sudo``, so on an apk host ``doas`` is usually the escalator to reach
    # for: reemote5 runs it passwordless as ``doas -n``, which needs a
    # ``permit nopass`` rule for the login user in ``/etc/doas.conf`` and
    # raises ``PrivilegeError`` without one. ``**kwargs`` absorbs pyinfra's
    # global arguments (``op()`` already strips ``name`` and the
    # ``sudo``/``su``/``doas`` escalation flags).
    #
    # ``no_cache`` and ``no_scripts`` are apk-native modifiers with no
    # apt/dnf equivalent -- apt's ``no_recommends``/``allow_downgrades`` and
    # dnf's ``nobest`` are the closest cousins, but neither tool exposes a
    # cache-bypass or script-skip flag on its package operation -- so they
    # are a deliberate apk-only asymmetry on the shared package-manager
    # surface. They affect only how the install/upgrade runs, never what the
    # ``ApkInstalledPackages`` fact reports, so idempotence is unchanged and the
    # removal direction (``present=False``) ignores both.
    #
    # Deliberate deviations from pyinfra: the ``update`` and ``upgrade``
    # options -- which prefix the operation with a whole-system ``apk update``
    # / ``apk upgrade`` -- are deliberately dropped, so those keywords are
    # absorbed by ``**kwargs`` with no effect; reemote5 keeps the
    # whole-system refresh/upgrade out of the package operation.

    reasons: list[str] = []

    # Normalize: pyinfra accepts a bare string, a list, or None.
    if packages is None:
        requested: tuple[str, ...] = ()
    elif isinstance(packages, str):
        requested = (packages,)
    else:
        requested = tuple(packages)

    if requested:
        installed = await ctx.facts.get(ApkInstalledPackages)
        # The fact reports one entry per installed name-and-version; group
        # the versions by name so the lookups below read as the version-list
        # comparison pyinfra's ``ensure_packages`` does.
        versions_by_name: dict[str, list[str]] = {}
        for entry in installed:
            versions_by_name.setdefault(entry["name"], []).append(entry["version"])

        def is_installed(package: str) -> bool:
            name, version = _split_version(package)
            versions = versions_by_name.get(name)
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
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Manage an apk virtual package group..

    Args:
        ctx (OpContext): Execution context for the operation.
        tag (str): The virtual package name apk records in ``/etc/apk/world``
            -- the group's identity, created with ``apk add -t <tag>`` and
            removed with ``apk del <tag>``.
        packages (str | list[str] | tuple[str, ...] | None, optional): The
            member packages the virtual group depends on; a single string, a
            list/tuple, or None. Prefer tuples when building requests via
            ``op()``, since ``OperationRequest`` kwargs must stay hashable.
            Versions can be pinned as ``<pkg>=<version>``. Defaults to None.
        present (bool, optional): Whether the virtual group should exist.
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated apk virtual package commands.

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apk/apk_virtual.py``.
    """

    # A reemote5 original: pyinfra's ``apk`` namespace ships only ``upgrade``,
    # ``update`` and ``packages`` -- no virtual-group management -- so this
    # operation has no upstream counterpart. Alpine's ``apk add -t <tag>
    # <packages>`` (``--virtual``) creates a temporary virtual package ``tag``
    # that depends on ``packages``, records ``tag`` in ``/etc/apk/world``, and
    # lets the whole group be torn down later with a single ``apk del <tag>`` --
    # the build-dependency pattern a compile step wraps itself in so its
    # toolchain does not linger on the finished image.
    #
    # Idempotence reads ``ApkExplicitPackages`` (the world entries): the group
    # is present exactly when ``tag`` matches an entry's ``name``.
    # ``present=True`` creates it with
    # ``apk add -t`` only when ``tag`` is absent (and only when at least one
    # member package is requested); ``present=False`` removes it with ``apk del``
    # only when ``tag`` is present. A deliberate simplification: the fact's
    # entries carry the world names and version constraints only, so a changed
    # member set for an already-existing
    # ``tag`` is NOT detected -- the group reads as present and the operation is a
    # no-op -- because apk records a virtual group's identity in world, not its
    # membership, and comparing members would need the fuzzy version-constrained
    # ``depends`` strings ``ApkPackage`` reports. Creating and removing both write
    # the root-owned apk database and world file, so flag the request
    # ``sudo=True``, ``doas=True`` or ``su=True`` -- stock Alpine ships no
    # ``sudo``, so ``doas`` is usually the escalator on an apk host (see
    # ``packages`` for its passwordless ``doas -n`` prerequisites). ``**kwargs``
    # absorbs pyinfra's global arguments (``op()`` already strips ``name`` and the
    # ``sudo``/``su``/``doas`` escalation flags).

    reasons: list[str] = []

    # Normalize: pyinfra-style operations accept a bare string, a list, or None.
    if packages is None:
        requested: tuple[str, ...] = ()
    elif isinstance(packages, str):
        requested = (packages,)
    else:
        requested = tuple(packages)

    world = await ctx.facts.get(ApkExplicitPackages)
    exists = any(entry["name"] == tag for entry in world)

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
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Hold packages back from upgrades, or release held ones (apk pins).

    Args:
        ctx (OpContext): Execution context for the operation.
        packages (str | list[str] | tuple[str, ...]): Package names to hold or
            unhold; a single string or a list/tuple. Prefer tuples when
            building requests via ``op()``, since ``OperationRequest`` kwargs
            must stay hashable.
        present (bool, optional): Whether the hold should be present
            (``True``, pin the package) or removed (``False``, release the
            pin). Defaults to True.
        version (str | None, optional): The optional version constraint to pin
            to -- a bare ``1.2.3-r0`` (normalized to ``=1.2.3-r0``) or an
            operator-led ``~1.2``/``<2.0``. When omitted with ``present=True``,
            each package is pinned to its currently installed version
            (``ApkInstalledPackages``' newest); ignored on the ``present=False``
            direction. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated apk hold commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apk/apk_hold.py``.
    """

    # The apk counterpart of ``apt.mark``: pyinfra ships no apk hold, and apk
    # has no ``apt-mark`` verb -- it keeps a package out of upgrades by pinning
    # a version constraint into ``/etc/apk/world``. Idempotent on
    # ``ApkHeldPackages`` (the ``ApkPackageVersion`` entries, looked up by
    # ``name`` and compared on
    # the entry's ``version`` -- the world constraint, operator included):
    # ``present=True`` yields one
    # ``ApkHoldCommand`` for only the requested names whose current pin differs
    # from the wanted constraint (a name already pinned to it is a no-op), and
    # ``present=False`` one for only the names currently held. The command keeps
    # the requested order (stable across phases).
    #
    # A deliberate consequence of pinning to the installed version: with
    # ``version`` omitted, a requested name that is *not* installed cannot be
    # pinned (there is no version to pin to) and is reported but not held --
    # pass ``version`` to pin a package that is not yet installed. Holding
    # writes the root-owned apk database and world file, so flag the request
    # ``sudo=True``, ``doas=True`` or ``su=True`` -- stock Alpine ships no
    # ``sudo``, so ``doas`` is usually the escalator on an apk host (see
    # ``packages`` for its passwordless ``doas -n`` prerequisites). ``**kwargs``
    # absorbs pyinfra's global arguments (``op()`` already strips ``name`` and the
    # ``sudo``/``su``/``doas`` escalation flags).

    # Normalize like apt.mark: a bare string is one name.
    requested = (packages,) if isinstance(packages, str) else tuple(packages)

    if not requested:
        ctx.reason = "no packages requested"
        return

    current = await ctx.facts.get(ApkHeldPackages)
    # The fact is a list of entries in world order; index it by name so the
    # lookups below read as before (a repeated name's last entry wins). The
    # entry's ``version`` is the world constraint, operator included.
    held_constraint = {entry["name"]: entry["version"] for entry in current}
    reasons: list[str] = []

    if present:
        to_hold: list[str] = []
        unpinnable: list[str] = []
        if version is not None:
            # An explicit constraint pins every requested name the same way;
            # no installed-version lookup is needed.
            pin = _normalize_constraint(version)
            for name in requested:
                if held_constraint.get(name) != pin:
                    to_hold.append(f"{name}{pin}")
        else:
            # Pin to each name's currently installed version -- read the fact
            # only on this branch (``version`` is a fixed kwarg, so the gather
            # set is identical in both phases).
            installed = await ctx.facts.get(ApkInstalledPackages)
            # One entry per installed name-and-version, sorted by name then
            # version, so grouping keeps each name's newest version last.
            versions_by_name: dict[str, list[str]] = {}
            for entry in installed:
                versions_by_name.setdefault(entry["name"], []).append(
                    entry["version"]
                )
            for name in requested:
                versions = versions_by_name.get(name)
                if not versions:
                    unpinnable.append(name)
                    continue
                pin = f"={versions[-1]}"
                if held_constraint.get(name) != pin:
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
        to_unhold = tuple(name for name in requested if name in held_constraint)
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

    The mutation ``apk.autoremove`` yields when ``ApkAutoremovablePackages`` names
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


async def autoremove(ctx: OpContext, **kwargs: Any) -> AsyncGenerator[Command]:
    """Remove orphan packages nothing reaches any more.

    Args:
        ctx (OpContext): Execution context for the operation.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The apk purge command, when orphans are purgable.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/apk/apk_autoremove.py``.
    """

    # The apk counterpart of ``apt.auto_remove`` and a reemote5 original:
    # pyinfra's ``apk`` namespace ships no cleanup verb, and apk itself has no
    # ``autoremove`` subcommand -- but it does have the ``--purge`` flag of
    # ``apk add``, which runs the solver and removes every installed package
    # no ``/etc/apk/world`` constraint (directly or transitively) needs any
    # more. The ``ApkAutoremovablePackages`` fact simulates exactly that plan and
    # names the orphans: a non-empty list yields the ``ApkPurgeCommand`` (the
    # names are already sorted, so the reason reads identically in both
    # phases), an empty list is a no-op. Purging needs root, so flag the
    # request ``sudo=True``, ``doas=True`` or ``su=True`` -- stock Alpine ships
    # no ``sudo``, so ``doas`` is usually the escalator on an apk host (see
    # ``packages`` for its passwordless ``doas -n`` prerequisites). ``**kwargs``
    # absorbs pyinfra's global arguments (``op()`` already strips ``name`` and the
    # ``sudo``/``su``/``doas`` escalation flags).
    #
    # Deliberate deviation from ``apt.auto_remove``: no ``wait_for_lock``
    # parameter, because apk uses no lock file the way apt's ``DPkg::Lock``
    # does -- concurrent apk invocations simply fail with a database-busy
    # error rather than waiting. A caller needing serialisation sequences the
    # operation behind its own guard.

    removable = await ctx.facts.get(ApkAutoremovablePackages)
    if removable:
        ctx.changed = True
        names = ", ".join(entry["name"] for entry in removable)
        ctx.reason = f"purgable packages will be removed: {names}"
        yield ApkPurgeCommand()
    else:
        ctx.reason = "no purgable packages"


__all__ = [
    "ApkAutoremovablePackages",
    "ApkExplicitPackages",
    "ApkHeldPackages",
    "ApkHoldCommand",
    "ApkInstallCommand",
    "ApkInstalledPackages",
    "ApkPackage",
    "ApkPackageInfo",
    "ApkPackageVersion",
    "ApkPurgeCommand",
    "ApkRemoveCommand",
    "ApkRepositories",
    "ApkRepository",
    "ApkUpgradeCommand",
    "ApkVirtualCommand",
    "autoremove",
    "hold",
    "packages",
    "virtual",
]
