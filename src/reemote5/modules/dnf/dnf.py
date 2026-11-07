# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """The ``dnf`` module: facts and operations in one source.
#
# Fact side (design doc section 5): ``DnfPackages``, the fact read by
# ``dnf.packages`` to learn which packages are already installed (sorted
# ``DnfPackageInfo`` entries grouping each name's installed versions),
# ``DnfRepositories``, the configured dnf repositories (``DnfRepository``
# entries with their open-ended config options in an ``options`` sub-dict),
# ``DnfRepoFile``, the raw content of a single ``.repo`` file (read by
# ``dnf.repo``), ``DnfRepoList``, the runtime repository list (enabled and
# disabled, as plain ``DnfRepoListEntry`` TypedDict-shaped dicts),
# ``DnfEnabledModules``, ``DnfDisabledModules`` and ``DnfInstalledModules``, the
# enabled, disabled and installed dnf modules (shared ``DnfModule``
# name-and-stream entries), and
# ``DnfGroups``/``DnfAvailableGroups``, the installed and available package
# groups (shared ``DnfGroup`` entries), and ``RpmPackage``, the
# name/version (``RpmPackageInfo``) of a ``.rpm`` file or an
# installed package that ``dnf.rpm`` reads to decide idempotence. Like every
# fact they are phase-unaware: each ``process`` coroutine just gathers the raw
# value.
#
# Operation side (design doc section 6): ``dnf.packages``, ``dnf.update``, ``dnf.repo``,
# ``dnf.rpm``, ``dnf.key`` and ``dnf.module`` plus the typed command descriptors
# they yield (``DnfInstallCommand``/``DnfRemoveCommand``/``DnfUpdateCommand``/
# ``DnfCleanCommand``/``DnfRepoUploadCommand``/``DnfRepoInstallCommand``/
# ``DnfRepoRemoveCommand``/``RpmInstallCommand``/
# ``DnfKeyUploadCommand``/``DnfKeyImportCommand``/``DnfModuleEnableCommand``/
# ``DnfModuleDisableCommand``). Operations are
# async generators that receive an ``OpContext``, await facts, set
# ``ctx.changed``/``ctx.reason``, and yield command descriptors; they never touch
# the host directly.
#
# ``dnf.packages`` mirrors pyinfra's option set (``present``, ``latest``,
# ``update``, ``clean``, ``nobest``, ``extra_install_args``,
# ``extra_uninstall_args``) with the same names and semantics. ``dnf.repo``
# mirrors pyinfra's ``dnf.repo`` for the manual-construction case (``present``,
# ``baseurl``, ``description``, ``enabled``, ``gpgcheck``, ``gpgkey``);
# pyinfra's URL source is dropped, since reemote5 has no download operation.
# ``dnf.rpm`` mirrors pyinfra's ``dnf.rpm`` (``src``, ``present``) for a ``.rpm``
# file already on the target host; pyinfra's URL source -- which downloads the
# file mid-deploy -- is dropped for the same reason, so a URL ``src`` raises
# ``ValueError``. ``dnf.key`` mirrors pyinfra's non-idempotent ``dnf.key`` but
# takes the GPG key content as an IO-like *stream* -- reemote5 has no
# controller-side file locations -- spooling it to a ``/tmp`` path over scp and
# running an elevated ``rpm --import`` on it, so a ``str`` ``src`` raises
# ``ValueError``. ``dnf.update`` mirrors pyinfra's non-idempotent ``dnf.update``:
# it reads no fact and always runs ``dnf update -y`` over every package.
# """

import io
import re
import shlex
from collections.abc import AsyncGenerator, Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import IO, Any, NotRequired, TypedDict
from urllib.parse import urlparse

from ...commands import Command, TransferCommand
from ...core import OpContext
from ...hosts import Host

# --- Facts -------------------------------------------------------------------


class DnfPackageInfo(TypedDict):
    """
    Attributes:
        name: The package name, exactly as ``rpm -qa`` prints it.
        versions: The package's installed versions (rpm's
            ``VERSION-RELEASE``), de-duplicated and sorted -- every
            version groups under one name, so a multi-version package
            (``kernel``) carries all its installed versions here.
    """

    # The package entry of ``DnfPackages`` -- the dnf counterpart of apt's
    # ``AptPackageInfo``: one shape grouping every installed version under
    # one name, where apk's ``ApkPackageVersion`` entries carry one
    # name-and-version pair each.
    #
    # A TypedDict rather than a class instance, like ``AptPackageInfo``:
    # entries are ordinary dicts, so equality against a literal dict works
    # and ``json.dumps`` serializes them directly. The ``Info`` suffix
    # follows the ``AptPackageInfo`` / ``ApkPackageInfo`` precedent.

    name: str
    versions: list[str]


class DnfPackages:
    """The installed dnf packages, as a sorted list of ``DnfPackageInfo`` dicts.

    **Commands:**

    ```bash
    rpm -qa --queryformat '%{NAME} %{VERSION}-%{RELEASE}\\n'
    ```

    **Returns:**

    ```python
    list[DnfPackageInfo]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/dnf/dnf_packages.py``.
    """

    # Mirrors pyinfra's ``dnf.DnfPackages`` with one deliberate extension:
    # pyinfra's query prints names only, while the query format here also
    # prints each package's ``VERSION-RELEASE`` (the same version string
    # ``RpmPackage`` reports), so every installed version is kept in the
    # entry's ``versions`` list rather than dropped. Names are
    # de-duplicated and sorted, versions grouped per name -- the apt-side
    # convention (``AptPackages``' sorted version lists) rather than
    # pyinfra's runtime set -- so reads stay deterministic and
    # JSON-printable for the ``gather_fact`` example scripts.

    #: Shell used to list installed package names and versions over SSH.
    COMMAND = "rpm -qa --queryformat '%{NAME} %{VERSION}-%{RELEASE}\n'"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[DnfPackageInfo]:
        """Parse ``rpm -qa`` rows into sorted, unique entries.

        Each non-blank line carries one package name and its
        ``VERSION-RELEASE``, whitespace-separated; versions group under
        their name, de-duplicated and sorted, and the entries are sorted
        by name. Pure and testable.
        """
        versions_by_name: dict[str, set[str]] = {}
        for line in output:
            tokens = line.split()
            if len(tokens) != 2:
                continue
            name, version = tokens
            versions_by_name.setdefault(name, set()).add(version)
        return [
            {"name": name, "versions": sorted(versions_by_name[name])}
            for name in sorted(versions_by_name)
        ]

    @classmethod
    async def process(cls, host: Host) -> list[DnfPackageInfo]:
        packages = getattr(host, "packages", None)
        if packages is not None:
            # SimulatedHost seam: the name -> versions state store the
            # mutators edit, projected into sorted entries (the
            # ``apt_packages`` precedent). Copy each versions list so
            # callers never mutate simulated state.
            return [
                {"name": name, "versions": sorted(set(versions))}
                for name, versions in sorted(packages.items())
            ]
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class DnfRepository(TypedDict):
    """
    Attributes:
        repoid: The repository id (the ``[section]`` header).
        name: The human-readable repository name (the ``name=`` option,
            defaulting to the section id).
        filename: The config/``.repo`` file the section was read from;
            absent when no filename marker preceded it.
        options: The section's remaining ``key=value`` options
            (``baseurl``, ``enabled``, ``gpgcheck``, ...) verbatim --
            repo options are open-ended, so they ride in a sub-dict
            rather than as fixed fields (the ``AptSource`` precedent).
    """

    # One configured repository (``DnfRepositories``' entry).
    #
    # A TypedDict rather than a class instance, like ``AptSource``: entries
    # are ordinary dicts, so equality against a literal dict works and
    # ``json.dumps`` serializes them directly. The identifying fields are
    # fixed; the open-ended ``key=value`` config lines a closed TypedDict
    # cannot express stay verbatim in ``options``.

    repoid: str
    name: str
    filename: NotRequired[str]
    options: dict[str, str]


class DnfRepositories:
    """The configured dnf repositories, as a list of ``DnfRepository`` dicts.

    **Commands:**

    ```bash
    for f in /etc/dnf.conf /etc/dnf.repos.d/*.repo /etc/yum.repos.d/*.repo; do [ -f "$f" ] && { printf "#reemote5-filename: %s\\n" "$f"; cat "$f"; }; done || true
    ```

    **Returns:**

    ```python
    list[DnfRepository]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/dnf/dnf_repositories.py``.
    """

    # Mirrors pyinfra's ``dnf.DnfRepositories``: every ``[section]`` of the dnf
    # config and ``.repo`` files becomes one ``DnfRepository`` entry with
    # ``repoid``/``name`` (both defaulting to the section id), the source
    # ``filename``, and each remaining ``key=value`` option in ``options``.

    #: Marker prefixing each cat'd file in ``COMMAND``'s output, naming the
    #: source file so ``parse`` can attribute a repository to its ``.repo``
    #: file (pyinfra's ``REPO_FILENAME_MARKER``).
    FILENAME_MARKER = "#reemote5-filename:"

    #: Cat the dnf config and every ``.repo`` file, each prefixed by a marker
    #: line naming the source file. The shell expands the globs and skips
    #: missing files (including unexpanded globs); the trailing ``|| true``
    #: keeps a non-zero exit from failing the gather.
    COMMAND = (
        "for f in /etc/dnf.conf /etc/dnf.repos.d/*.repo /etc/yum.repos.d/*.repo; do "
        f'[ -f "$f" ] && {{ printf "{FILENAME_MARKER} %s\\n" "$f"; cat "$f"; }}; '
        "done || true"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[DnfRepository]:
        """Parse INI-style repo config lines into a list of repository dicts.

        A ``[section]`` header starts a new repository whose ``repoid`` and
        ``name`` default to the section id; a preceding filename-marker line
        supplies its ``filename``; ``key=value`` lines fill in ``options``
        (a ``name=`` line overrides the default name); ``#`` comments and
        blank lines are ignored.
        """
        repos: list[DnfRepository] = []
        current: DnfRepository | None = None
        current_file: str | None = None

        for raw in output:
            line = raw.strip()
            if not line:
                continue

            if line.startswith(cls.FILENAME_MARKER):
                if current is not None:
                    repos.append(current)
                    current = None
                current_file = line[len(cls.FILENAME_MARKER) :].strip() or None
                continue

            if line.startswith("#"):
                continue

            if line.startswith("["):
                if current is not None:
                    repos.append(current)
                section = line[1:-1]
                current = {"repoid": section, "name": section, "options": {}}
                if current_file is not None:
                    current["filename"] = current_file

            if current is not None and "=" in line:
                key, value = re.split(r"\s*=\s*", line, maxsplit=1)
                if key == "name":
                    current["name"] = value
                else:
                    current["options"][key] = value

        if current is not None:
            repos.append(current)

        return repos

    @classmethod
    async def process(cls, host: Host) -> list[DnfRepository]:
        repositories = getattr(host, "repositories", None)
        if repositories is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy
            # each entry (and its options dict) so callers never mutate
            # simulated state.
            copies: list[DnfRepository] = []
            for repo in repositories:
                copy = dict(repo)
                copy["options"] = dict(repo["options"])
                copies.append(copy)
            return copies
        # Real transport: cat the repo files over asyncssh and parse the INI.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class DnfRepoFile:
    """The current content of one ``.repo`` file, or None when it is absent.

    **Commands:**

    ```bash
    test -e <path> && { printf '#reemote5-repofile\\n'; cat -- <path>; }
    ```

    **Returns:**

    ```python
    str | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/dnf/dnf_repo.py``.
    """

    # The fact ``dnf.repo`` reads to make its write idempotent: where
    # ``DnfRepositories`` parses *every* configured repository into option
    # dicts, this reports the raw text of a *single* file so the operation can
    # compare it against the content it would write. Absence is reported as
    # ``None`` -- never the empty string -- so a file that exists but is empty
    # stays distinguishable from one that is missing, which is what lets
    # ``dnf.repo(present=False)`` remove an empty leftover.

    #: Marker printed before the ``cat``'d content, only when ``test -e``
    #: passes, so ``process`` can tell an existing (possibly empty) file from
    #: a missing one (no marker on stdout).
    MARKER = "#reemote5-repofile"

    @classmethod
    def command(cls, path: str) -> str:
        """Shell printing ``MARKER`` then the file's content, only if it exists.

        The ``test -e`` guard means a missing file produces no output at all
        (not even the marker), while an existing one -- empty or not -- always
        leads with the marker line.
        """
        quoted = shlex.quote(path)
        return f"test -e {quoted} && {{ printf '{cls.MARKER}\\n'; cat -- {quoted}; }}"

    @classmethod
    async def process(cls, host: Host, path: str) -> str | None:
        remote_files = getattr(host, "remote_files", None)
        if remote_files is not None:
            # SimulatedHost seam: decode the content the simulated remote
            # filesystem holds at the path (None when absent). The same store
            # backs files.put/files.get, so a simulated dnf.repo write -- which
            # stores its content here -- reads back through this fact.
            data = remote_files.get(path)
            return data.decode("utf-8") if data is not None else None
        # Real transport: cat the file over asyncssh behind the marker, so a
        # missing file (no marker) reads as None rather than empty content.
        stdout = await host.run_shell(cls.command(path))
        prefix = f"{cls.MARKER}\n"
        if not stdout.startswith(prefix):
            return None
        return stdout[len(prefix) :]


class DnfModule(TypedDict):
    """
    Attributes:
        name: The dnf module name (an application-stream family).
        stream: The module stream the row's flag applies to. For
            ``DnfDisabledModules`` a simulated whole-module disable
            carries ``""`` -- ``dnf module disable`` names no stream, so
            the simulation has none to report (the real listing's rows
            always carry one).
    """

    # One module-and-stream row -- the shared entry of every dnf module
    # fact: ``DnfEnabledModules``, ``DnfDisabledModules`` and
    # ``DnfInstalledModules`` all return ``list[DnfModule]``, so one typed
    # shape answers every module-state question the namespace asks (the
    # ``ApkPackageVersion`` shared-entry precedent).
    #
    # A TypedDict rather than a class instance: entries are ordinary dicts,
    # so equality against a literal dict works and ``json.dumps`` serializes
    # them directly. A module disabled across several streams contributes
    # one entry per flagged row, keeping each stream rather than collapsing
    # to the bare name.

    name: str
    stream: str


class DnfEnabledModules:
    """The enabled dnf modules, as a sorted list of ``DnfModule`` dicts.

    **Commands:**

    ```bash
    dnf module list --enabled
    ```

    **Returns:**

    ```python
    list[DnfModule]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/dnf/dnf_enabled_modules.py``.
    """

    # Mirrors pyinfra's ``dnf.DnfEnabledModules``: every row of the
    # ``dnf module list --enabled`` table carrying an ``[e]`` flag becomes a
    # ``DnfModule`` entry naming the module and its enabled stream; headers,
    # blank lines and the trailing hint line are ignored.

    #: Shell used to list the enabled modules over SSH.
    COMMAND = "dnf module list --enabled"

    #: Matches the ``[e]`` enabled flag on a stream but not the ``[e]nabled``
    #: word in the table's trailing ``Hint:`` line (pyinfra's
    #: ``_ENABLED_FLAG``).
    ENABLED_FLAG = re.compile(r"\[e\](?!\w)")

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[DnfModule]:
        """Parse ``dnf module list --enabled`` rows into sorted entries.

        Only lines carrying the ``[e]`` flag are kept; the first two
        whitespace-separated tokens of such a line are the module name and
        its enabled stream. Entries are de-duplicated and sorted by name,
        then stream.
        """
        seen: set[tuple[str, str]] = set()
        for line in output:
            if not cls.ENABLED_FLAG.search(line):
                continue
            tokens = line.split()
            if len(tokens) < 2:
                continue
            seen.add((tokens[0], tokens[1]))
        return [{"name": name, "stream": stream} for name, stream in sorted(seen)]

    @classmethod
    async def process(cls, host: Host) -> list[DnfModule]:
        enabled_modules = getattr(host, "enabled_modules", None)
        if enabled_modules is not None:
            # SimulatedHost seam: the name -> stream state store the
            # mutators edit, projected into sorted entries. Freshly built
            # dicts so callers never mutate simulated state.
            return [
                {"name": name, "stream": stream}
                for name, stream in sorted(enabled_modules.items())
            ]
        # Real transport: list the enabled modules over asyncssh and parse.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class DnfDisabledModules:
    """The disabled dnf modules, as a sorted list of ``DnfModule`` dicts.

    **Commands:**

    ```bash
    dnf module list --disabled
    ```

    **Returns:**

    ```python
    list[DnfModule]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/dnf/dnf_disabled_modules.py``.
    """

    # Mirrors pyinfra's ``dnf.DnfDisabledModules``: every row of the
    # ``dnf module list --disabled`` table carrying an ``[x]`` flag becomes a
    # ``DnfModule`` entry naming the module and the flagged stream; a module
    # disabled across several streams contributes one entry per row, and the
    # entries are de-duplicated and sorted by name, then stream. Headers,
    # blank lines and the trailing hint line are ignored.

    #: Shell used to list the disabled modules over SSH.
    COMMAND = "dnf module list --disabled"

    #: Matches the ``[x]`` disabled flag on a stream but not the ``[x]disabled``
    #: word in the table's trailing ``Hint:`` line (pyinfra's
    #: ``_DISABLED_FLAG``).
    DISABLED_FLAG = re.compile(r"\[x\](?!\w)")

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[DnfModule]:
        """Parse ``dnf module list --disabled`` rows into sorted entries.

        Only lines carrying the ``[x]`` flag are kept; the first two
        whitespace-separated tokens of such a line are the module name and
        the flagged stream. Entries are de-duplicated across repeated rows
        and sorted by name, then stream.
        """
        seen: set[tuple[str, str]] = set()
        for line in output:
            if not cls.DISABLED_FLAG.search(line):
                continue
            tokens = line.split()
            if len(tokens) < 2:
                continue
            seen.add((tokens[0], tokens[1]))
        return [{"name": name, "stream": stream} for name, stream in sorted(seen)]

    @classmethod
    async def process(cls, host: Host) -> list[DnfModule]:
        disabled_modules = getattr(host, "disabled_modules", None)
        if disabled_modules is not None:
            # SimulatedHost seam: the disabled-name state store the
            # mutators edit, projected into sorted entries. A simulated
            # ``dnf module disable`` names no stream (it disables the whole
            # module), so each entry carries "" -- the placeholder the
            # real listing's per-stream rows never need.
            return [
                {"name": name, "stream": ""} for name in sorted(disabled_modules)
            ]
        # Real transport: list the disabled modules over asyncssh and parse.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class DnfInstalledModules:
    """The installed dnf modules, as a sorted list of ``DnfModule`` dicts.

    **Commands:**

    ```bash
    dnf module list --installed
    ```

    **Returns:**

    ```python
    list[DnfModule]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/dnf/dnf_installed_modules.py``.
    """

    # The counterpart of ``DnfEnabledModules``: where that fact reports the
    # streams dnf has *enabled* (``[e]``), this one reports the streams that
    # actually have packages *installed* (``[i]``) out of the
    # ``dnf module list --installed`` table. A module can be enabled without any
    # of its packages installed, or installed on a stream that is not the enabled
    # default, so the two facts answer different questions. Every ``[i]``-flagged
    # row becomes a ``DnfModule`` entry naming the module and its installed
    # stream; headers, blank lines and the trailing hint line are ignored.

    #: Shell used to list the installed modules over SSH.
    COMMAND = "dnf module list --installed"

    #: Matches the ``[i]`` installed flag on a stream but not the ``[i]nstalled``
    #: word in the table's trailing ``Hint:`` line.
    INSTALLED_FLAG = re.compile(r"\[i\](?!\w)")

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[DnfModule]:
        """Parse ``dnf module list --installed`` rows into sorted entries.

        Only lines carrying the ``[i]`` flag are kept; the first two
        whitespace-separated tokens of such a line are the module name and its
        installed stream. Entries are de-duplicated and sorted by name,
        then stream.
        """
        seen: set[tuple[str, str]] = set()
        for line in output:
            if not cls.INSTALLED_FLAG.search(line):
                continue
            tokens = line.split()
            if len(tokens) < 2:
                continue
            seen.add((tokens[0], tokens[1]))
        return [{"name": name, "stream": stream} for name, stream in sorted(seen)]

    @classmethod
    async def process(cls, host: Host) -> list[DnfModule]:
        installed_modules = getattr(host, "installed_modules", None)
        if installed_modules is not None:
            # SimulatedHost seam: the name -> stream state store, projected
            # into sorted entries. Freshly built dicts so callers never
            # mutate simulated state.
            return [
                {"name": name, "stream": stream}
                for name, stream in sorted(installed_modules.items())
            ]
        # Real transport: list the installed modules over asyncssh and parse.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class DnfGroup(TypedDict):
    """
    Attributes:
        name: The package-group name, exactly as ``dnf group list``
            prints it (environment groups and regular groups alike).
    """

    # One package group -- the shared entry of ``DnfGroups`` and
    # ``DnfAvailableGroups``: one typed shape answers both group-list
    # questions (the ``DnfModule`` shared-entry precedent).
    #
    # A TypedDict rather than a class instance: entries are ordinary dicts,
    # so equality against a literal dict works and ``json.dumps`` serializes
    # them directly. ``dnf group list`` prints names only, so the entry
    # carries just the name.

    name: str


def _parse_group_list(output: Iterable[str], *, section: str) -> list[DnfGroup]:
    """Collect the group names listed under ``section`` headers.

    ``dnf group list`` prints its section headers at column 0, each ending in a
    colon (``Installed Groups:``, ``Available Environment Groups:``, ...), and
    lists every group name on its own *indented* line beneath the header it
    belongs to. A group name is kept only while the current header starts with
    ``section`` (``"Installed"`` for ``DnfGroups``, ``"Available"`` for
    ``DnfAvailableGroups``); any other header -- or a blank line, or a stray
    non-indented prompt line -- switches collection off, so environment-group
    and regular-group names are gathered together while names from the unwanted
    sections are not. Names are de-duplicated and returned as ``DnfGroup``
    entries sorted by name (the apt-side determinism/JSON convention, not
    pyinfra's runtime set).
    """
    names: set[str] = set()
    in_section = False
    for raw in output:
        if not raw.strip():
            continue
        # A header sits at column 0 and ends with a colon; a group name is
        # indented beneath it, so indentation alone tells the two apart.
        if not raw[0].isspace():
            header = raw.strip()
            in_section = header.endswith(":") and header.startswith(section)
            continue
        if in_section:
            names.add(raw.strip())
    return [{"name": name} for name in sorted(names)]


class DnfGroups:
    """The installed dnf groups, as a sorted list of ``DnfGroup`` dicts.

    **Commands:**

    ```bash
    dnf group list --installed
    ```

    **Returns:**

    ```python
    list[DnfGroup]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/dnf/dnf_groups.py``.
    """

    # Fills a gap pyinfra leaves open: there is no way to ask which package
    # *groups* are already installed, so a ``dnf group install`` cannot be made
    # idempotent. This fact parses ``dnf group list --installed`` and returns the
    # installed group names -- both environment groups (``Installed Environment
    # Groups:``) and regular groups (``Installed Groups:``) -- as sorted
    # ``DnfGroup`` entries, so a later group operation can skip the ones already
    # present.

    #: Shell used to list the installed groups over SSH.
    COMMAND = "dnf group list --installed"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[DnfGroup]:
        """Parse ``dnf group list --installed`` into sorted entries.

        Only the names indented under an ``Installed ... Groups:`` header are
        kept; the ``Available`` sections (and any prompt or blank line) are
        ignored. Pure and testable.
        """
        return _parse_group_list(output, section="Installed")

    @classmethod
    async def process(cls, host: Host) -> list[DnfGroup]:
        installed_groups = getattr(host, "installed_groups", None)
        if installed_groups is not None:
            # SimulatedHost seam: the group-name state store, projected
            # into sorted entries. Freshly built dicts so callers never
            # mutate simulated state.
            return [{"name": name} for name in sorted(installed_groups)]
        # Real transport: list the installed groups over asyncssh and parse.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class DnfAvailableGroups:
    """The available (not-yet-installed) dnf groups, as ``DnfGroup`` dicts.

    **Commands:**

    ```bash
    dnf group list --available
    ```

    **Returns:**

    ```python
    list[DnfGroup]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/dnf/dnf_available_groups.py``.
    """

    # The counterpart of ``DnfGroups``: parses ``dnf group list --available`` and
    # returns the group names dnf could install -- both environment groups
    # (``Available Environment Groups:``) and regular groups (``Available
    # Groups:``) -- as sorted ``DnfGroup`` entries. Useful to check that a group
    # name is valid before installing it, and to decide whether an install is
    # needed at all.

    #: Shell used to list the available groups over SSH.
    COMMAND = "dnf group list --available"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[DnfGroup]:
        """Parse ``dnf group list --available`` into sorted entries.

        Only the names indented under an ``Available ... Groups:`` header are
        kept; the ``Installed`` sections (and any prompt or blank line) are
        ignored. Pure and testable.
        """
        return _parse_group_list(output, section="Available")

    @classmethod
    async def process(cls, host: Host) -> list[DnfGroup]:
        available_groups = getattr(host, "available_groups", None)
        if available_groups is not None:
            # SimulatedHost seam: the group-name state store, projected
            # into sorted entries. Freshly built dicts so callers never
            # mutate simulated state.
            return [{"name": name} for name in sorted(available_groups)]
        # Real transport: list the available groups over asyncssh and parse.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class DnfRepoListEntry(TypedDict):
    """
    Attributes:
        repoid: The repository id (the row's first column).
        name: The human-readable repository name (possibly multi-word).
        status: ``"enabled"`` or ``"disabled"`` when the listing carries a
            status column; absent for a two-column ``dnf repolist``.
    """

    # One runtime repository row (``DnfRepoList``'s entry), as a plain dict.
    #
    # A TypedDict rather than a class instance, like ``AptSource``: entries
    # are ordinary dicts, so equality against a literal dict works and
    # ``json.dumps`` serializes them directly. (``DnfRepositories``' entries
    # are ``DnfRepository`` dicts: their open-ended ``key=value`` config
    # options ride in an ``options`` sub-dict a closed TypedDict expresses
    # only that way.)

    repoid: str
    name: str
    status: NotRequired[str]


class DnfRepoList:
    """The runtime dnf repositories, as a list of ``DnfRepoListEntry`` dicts.

    **Commands:**

    ```bash
    dnf repolist --all
    ```

    **Returns:**

    ```python
    list[DnfRepoListEntry]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/dnf/dnf_repo_list.py``.
    """

    # Where ``DnfRepositories`` parses the ``.repo`` *configuration* files -- and
    # so cannot tell which repositories dnf actually has loaded, or whether a
    # configured repo is enabled -- this fact reports the *runtime* list from
    # ``dnf repolist --all``: every repository dnf knows about, enabled or
    # disabled. Each row becomes a dict with the ``repoid``, its human-readable
    # ``name`` and, when the listing carries one, its ``status`` (``enabled`` or
    # ``disabled``).

    #: Shell used to list every repository (enabled and disabled) over SSH.
    COMMAND = "dnf repolist --all"

    #: The status literals ``dnf repolist --all`` puts in its last column. A
    #: trailing token matching one is the status; anything else stays part of
    #: the repository name (so a two-column ``dnf repolist`` still parses).
    STATUSES = ("enabled", "disabled")

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[DnfRepoListEntry]:
        """Parse ``dnf repolist --all`` rows into a list of repository dicts.

        The first whitespace-separated token is the ``repoid`` and the last --
        when it is one of ``STATUSES`` -- is the ``status``; everything in
        between is the (possibly multi-word) ``name``. The ``repo id`` header
        row and blank lines are skipped. Pure and testable.
        """
        repos: list[DnfRepoListEntry] = []
        for raw in output:
            line = raw.strip()
            if not line:
                continue
            tokens = line.split()
            if tokens[:2] == ["repo", "id"]:  # the table header row
                continue
            if len(tokens) < 2:
                continue
            repoid, rest = tokens[0], tokens[1:]
            status = None
            if rest[-1] in cls.STATUSES:
                status = rest.pop()
            if not rest:
                continue
            repo: DnfRepoListEntry = {"repoid": repoid, "name": " ".join(rest)}
            if status is not None:
                repo["status"] = status
            repos.append(repo)
        return repos

    @classmethod
    async def process(cls, host: Host) -> list[DnfRepoListEntry]:
        repo_list = getattr(host, "repo_list", None)
        if repo_list is not None:
            # SimulatedHost seam: read the in-memory state directly. Copy
            # each entry dict so callers never mutate simulated state.
            return [dict(repo) for repo in repo_list]
        # Real transport: list the repositories over asyncssh and parse.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class RpmPackageInfo(TypedDict):
    """
    Attributes:
        name: The package name rpm reports for the queried name/file.
        version: The package's version -- rpm's ``VERSION-RELEASE``.
    """

    # The name-and-version detail of one queried package (``RpmPackage``'s
    # answer). The ``Info`` suffix follows the ``ApkPackageInfo`` /
    # ``DebPackageInfo`` precedent: the bare singular slot is taken by the
    # fact class itself.
    #
    # A TypedDict rather than a class instance: the entry is an ordinary
    # dict, so equality against a literal dict works and ``json.dumps``
    # serializes it directly.

    name: str
    version: str


class RpmPackage:
    """The name and version of a ``.rpm`` file or an installed package.

    **Commands:**

    ```bash
    rpm --queryformat '%{NAME} %{VERSION}-%{RELEASE}\\n' -q <package> || ! test -e <package> || rpm --queryformat '%{NAME} %{VERSION}-%{RELEASE}\\n' -qp <package> 2> /dev/null
    ```

    **Returns:**

    ```python
    RpmPackageInfo | None
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/dnf/dnf_rpm.py``.
    """

    # Mirrors pyinfra's ``rpm.RpmPackage``: given a package *name* or a ``.rpm``
    # file *path*, returns a ``RpmPackageInfo`` entry (``version`` is
    # rpm's ``VERSION-RELEASE``), or None when the name is not installed and the
    # path is not an existing file. ``dnf.rpm`` reads it twice -- once for the
    # ``.rpm`` file, once for the installed package of that file's name -- and
    # compares the two versions to decide whether the file's package is already
    # installed at that version.

    #: rpm query format printing ``NAME VERSION-RELEASE`` on one line
    #: (pyinfra's ``rpm_query_format``).
    QUERY_FORMAT = "%{NAME} %{VERSION}-%{RELEASE}\\n"

    #: Matches the ``NAME VERSION-RELEASE`` line ``QUERY_FORMAT`` prints
    #: (pyinfra's ``rpm_regex``).
    RPM_REGEX = re.compile(r"^(\S+)\s+(\S+)$")

    @classmethod
    def command(cls, package: str) -> str:
        """Shell reporting ``package``'s name and version.

        Queries the *installed* package ``package`` by name first (``rpm -q``);
        if that fails and ``package`` is an existing file, queries the *file*
        (``rpm -qp``). A name that is neither installed nor an existing file
        produces no output -- the ``! test -e`` guard short-circuits the chain
        -- so ``process`` reports None rather than empty metadata.
        """
        query_format = shlex.quote(cls.QUERY_FORMAT)
        quoted = shlex.quote(package)
        return (
            f"rpm --queryformat {query_format} -q {quoted} || "
            f"! test -e {quoted} || "
            f"rpm --queryformat {query_format} -qp {quoted} 2> /dev/null"
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> RpmPackageInfo | None:
        """Parse ``rpm``'s ``NAME VERSION-RELEASE`` output into an entry.

        Returns the first matching line as a ``RpmPackageInfo`` dict, or None
        when no line matches (an unknown name, or an absent ``.rpm`` file).
        """
        for line in output:
            match = cls.RPM_REGEX.match(line.strip())
            if match:
                return {"name": match.group(1), "version": match.group(2)}
        return None

    @classmethod
    async def process(cls, host: Host, package: str) -> RpmPackageInfo | None:
        rpm_files = getattr(host, "rpm_files", None)
        if rpm_files is not None:
            # SimulatedHost seam: an installed name (``rpm_packages``) is
            # queried before a ``.rpm`` file path (``rpm_files``), mirroring
            # ``command``'s installed-before-file precedence.
            rpm_packages = host.rpm_packages
            if package in rpm_packages:
                return {"name": package, "version": rpm_packages[package]}
            info = rpm_files.get(package)
            return dict(info) if info is not None else None
        # Real transport: query the installed package / .rpm file over asyncssh.
        stdout = await host.run_shell(cls.command(package))
        return cls.parse(stdout.splitlines())


# --- Operations --------------------------------------------------------------


def _split_version(package: str) -> tuple[str, str | None]:
    """Split ``pkg=version`` into ``(name, version)``; version is None if absent.

    pyinfra's ``PkgInfo.from_possible_pair`` splits on the *last* ``=``
    (``rsplit``), so a version string carrying ``=`` stays intact -- the
    same split ``apt.packages`` uses.
    """
    name, sep, version = package.rpartition("=")
    return (name, version) if sep else (package, None)


@dataclass(frozen=True)
class DnfInstallCommand(Command):
    packages: tuple[str, ...]
    nobest: bool = False
    extra_args: str | None = None

    def shell(self) -> str:
        parts = ["dnf", "install", "-y"]
        if self.nobest:
            parts.append("--nobest")
        if self.extra_args:
            parts.append(self.extra_args)
        parts.extend(shlex.quote(package) for package in self.packages)
        return " ".join(parts)


@dataclass(frozen=True)
class DnfRemoveCommand(Command):
    packages: tuple[str, ...]
    extra_args: str | None = None

    def shell(self) -> str:
        parts = ["dnf", "remove", "-y"]
        if self.extra_args:
            parts.append(self.extra_args)
        parts.extend(shlex.quote(package) for package in self.packages)
        return " ".join(parts)


@dataclass(frozen=True)
class DnfUpdateCommand(Command):
    """``dnf update``: all packages when bare, just ``packages`` when given
    (the ``latest`` upgrade path). The bare form is what both ``dnf.update``
    and ``dnf.packages(update=True)`` yield."""

    packages: tuple[str, ...] = ()

    def shell(self) -> str:
        parts = ["dnf", "update", "-y"]
        parts.extend(shlex.quote(package) for package in self.packages)
        return " ".join(parts)


@dataclass(frozen=True)
class DnfCleanCommand(Command):
    """``dnf clean all`` (the ``clean`` option)."""

    def shell(self) -> str:
        return "dnf clean all"


async def packages(
    ctx: OpContext,
    packages: str | list[str] | tuple[str, ...] | None = None,
    present: bool = True,
    latest: bool = False,
    update: bool = False,
    clean: bool = False,
    nobest: bool = False,
    extra_install_args: str | None = None,
    extra_uninstall_args: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Install/remove/update dnf packages.

    Args:
        ctx (OpContext): Execution context for the operation.
        packages (str | list[str] | tuple[str, ...] | None, optional): Packages
            to ensure. Prefer tuples when building requests via ``op()``, since
            ``OperationRequest`` kwargs must stay hashable. Versions can be
            pinned as ``<pkg>=<version>``. Defaults to None.
        present (bool, optional): Whether the packages should be installed.
            Defaults to True.
        latest (bool, optional): Whether to upgrade installed packages without
            a pinned version. Defaults to False.
        update (bool, optional): Run ``dnf update`` before installing packages.
            Defaults to False.
        clean (bool, optional): Run ``dnf clean all`` before installing
            packages. Defaults to False.
        nobest (bool, optional): Add the ``--nobest`` option to the install
            command. Defaults to False.
        extra_install_args (str | None, optional): Additional arguments to the
            dnf install command. Defaults to None.
        extra_uninstall_args (str | None, optional): Additional arguments to
            the dnf remove command. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated dnf package commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/dnf/dnf_packages.py``.
    """

    # pyinfra ``dnf.packages`` parity.
    #
    # ``clean`` and ``update`` are stateless (always yield, like pyinfra's
    # non-idempotent operations). The ``DnfPackages`` fact reports each
    # name's installed versions, but the comparison stays name-only
    # (pyinfra parity): a pinned ``<pkg>=<version>`` is considered present
    # when *any* version of ``pkg`` is installed, and ``latest`` follows
    # pyinfra's name-only behavior: every installed, unpinned package is
    # upgraded without checking whether an upgrade actually exists.
    # ``kwargs`` absorbs any further keyword arguments (pyinfra's global
    # arguments are meta-parameters handled by ``op()`` here).
    reasons: list[str] = []

    if clean:
        ctx.changed = True
        reasons.append("dnf clean")
        yield DnfCleanCommand()

    if update:
        ctx.changed = True
        reasons.append("dnf update")
        yield DnfUpdateCommand()

    # Normalize: pyinfra accepts a bare string, a list, or None.
    if packages is None:
        requested: tuple[str, ...] = ()
    elif isinstance(packages, str):
        requested = (packages,)
    else:
        requested = tuple(packages)

    if requested:
        installed = await ctx.facts.get(DnfPackages)
        installed_names = {entry["name"] for entry in installed}

        if present:
            to_install = tuple(
                pkg for pkg in requested if _split_version(pkg)[0] not in installed_names
            )
            # ``latest``: installed, unpinned packages are upgrade candidates.
            to_upgrade = (
                tuple(
                    pkg
                    for pkg in requested
                    if _split_version(pkg)[1] is None and pkg in installed_names
                )
                if latest
                else ()
            )
            if to_install:
                ctx.changed = True
                reasons.append(f"{len(to_install)} package(s) to install")
                yield DnfInstallCommand(
                    packages=to_install,
                    nobest=nobest,
                    extra_args=extra_install_args,
                )
            if to_upgrade:
                ctx.changed = True
                reasons.append(f"{len(to_upgrade)} package(s) to upgrade")
                yield DnfUpdateCommand(packages=to_upgrade)
            if not to_install and not to_upgrade:
                reasons.append("all packages present")
        else:
            to_remove = tuple(
                pkg for pkg in requested if _split_version(pkg)[0] in installed_names
            )
            if to_remove:
                ctx.changed = True
                reasons.append(f"{len(to_remove)} package(s) to remove")
                yield DnfRemoveCommand(
                    packages=to_remove, extra_args=extra_uninstall_args
                )
            else:
                reasons.append("no packages to remove")
    elif not reasons:
        reasons.append("no packages requested")

    ctx.reason = "; ".join(reasons)


# --- dnf.update --------------------------------------------------------------


async def update(ctx: OpContext, **kwargs: Any) -> AsyncGenerator[Command]:
    """Update every dnf package on the host.

    Args:
        ctx (OpContext): Execution context for the operation.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The dnf update command, always (the operation is stateless).

    **Raises:**

    ```python
    None
    ```

    Examples:
        Examples can be found in ``examples/modules/dnf/dnf_update.py``.
    """

    # pyinfra ``dnf.update`` parity.
    #
    # Like pyinfra's ``dnf.update`` this is **non-idempotent**: it reads no
    # fact, so it always reports a change and yields one bare
    # ``DnfUpdateCommand`` (rendering ``dnf update -y``) on every run. There is
    # no cheap way to tell whether any package actually has a pending upgrade,
    # so dnf is simply invoked and is itself a no-op when the system is already
    # up to date. Updating the whole system needs root, so flag the request
    # ``sudo=True`` (or ``su=True``). ``**kwargs`` absorbs pyinfra's global
    # arguments (``op()`` already strips ``name``/``sudo``/``su``).
    ctx.changed = True
    ctx.reason = "dnf update"
    yield DnfUpdateCommand()


# --- dnf.repo ----------------------------------------------------------------

#: Directory dnf/yum reads ``.repo`` files from -- pyinfra's ``ensure_yum_repo``
#: default ``repo_directory``.
_REPO_DIRECTORY = "/etc/yum.repos.d/"

#: Where ``dnf.repo``'s transfer spools the generated ``.repo`` content before
#: the elevated install. A fixed path under world-writable ``/tmp`` so the
#: *unprivileged* scp can write it, and a constant so the value is identical
#: across the prepare and apply phases (a per-materialization temp name would
#: diverge and trip the determinism guard). ``DnfRepoInstallCommand`` removes
#: it again after installing the repo file.
_REPO_SPOOL_PATH = "/tmp/reemote5-dnf-repo.repo"


@dataclass(frozen=True)
class DnfRepoUploadCommand(TransferCommand):
    """Spool the generated ``.repo`` content to a ``/tmp`` path on the target.

    The first half of ``dnf.repo``'s write direction and a *non-shell*
    descriptor: it copies the content over the connection's scp channel via
    ``Host.upload_file`` rather than running shell text, so it has no
    ``shell()`` and its loggable form is the ``__str__`` rendering
    ``command_shell`` falls back to. Like ``AptSourcesFileUploadCommand``
    the content is not a caller-supplied stream but the repo text the
    operation itself generates, so the field is a plain ``str`` -- hashable
    and identical across phases -- wrapped in a fresh ``io.BytesIO`` only
    at transfer time. The copy rides as the login user (transfers are
    unprivileged, so the request's ``sudo`` does not apply to it), which is
    why the spool path is world-writable ``/tmp`` rather than the
    root-owned ``yum.repos.d`` directory.
    """

    content: str  # the .repo text the operation generated
    dest: str  # spool path on the target host

    def __str__(self) -> str:
        return f"scp <repo file> -> {self.dest}"

    async def transfer(self, host: Host) -> None:
        await host.upload_file(io.BytesIO(self.content.encode()), self.dest)


@dataclass(frozen=True)
class DnfRepoInstallCommand(Command):
    """Install the spooled ``.repo`` file ``0644``, then remove the spool file.

    The second half of ``dnf.repo``'s write direction and the elevated one:
    writing under ``/etc/yum.repos.d/`` needs root, so the request is
    flagged ``sudo=True`` and the whole sequence is wrapped in ``sh -c`` --
    a bare ``sudo`` prefix would elevate only the first command of the
    ``&&`` chain and leave the cleanup running as the login user, whereas
    ``sudo sh -c '...'`` runs every step as root, which can always remove
    the login-user spool file. The sequence creates the destination
    directory, copies the spooled content over the target file, chmods it
    ``0644`` (pyinfra's ``dnf.repo`` routes through ``files.put`` with
    ``mode="0644"``) and removes the spool file -- the
    ``AptSourcesFileInstallCommand`` pattern. All paths are shell-quoted.
    """

    spool: str  # where DnfRepoUploadCommand wrote the content
    dest: str  # the /etc/yum.repos.d/<src>.repo path

    def shell(self) -> str:
        spool = shlex.quote(self.spool)
        dest = shlex.quote(self.dest)
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        inner = (
            f"mkdir -p {parent} && "
            f"cp {spool} {dest} && "
            f"chmod 0644 {dest} && rm -f {spool}"
        )
        return f"sh -c {shlex.quote(inner)}"


@dataclass(frozen=True)
class DnfRepoRemoveCommand(Command):
    """``rm -f <path>``: delete a ``.repo`` file.

    The removal mutation ``dnf.repo(present=False)`` yields, only when the
    ``DnfRepoFile`` fact reports the file present. ``rm -f`` tolerates an
    absent path, and deleting under ``yum.repos.d`` needs root, so flag the
    request ``sudo=True`` (or ``su=True``). The path is shell-quoted.
    """

    path: str

    def shell(self) -> str:
        return f"rm -f {shlex.quote(self.path)}"


async def repo(
    ctx: OpContext,
    src: str,
    present: bool = True,
    baseurl: str | None = None,
    description: str | None = None,
    enabled: bool = True,
    gpgcheck: bool = True,
    gpgkey: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add/remove/update a dnf repository ``.repo`` file.

    Args:
        ctx (OpContext): Execution context for the operation.
        src (str): The repository name; the file is
            ``/etc/yum.repos.d/<src>.repo`` and ``<src>`` is its ``[section]``
            id.
        present (bool, optional): Whether the ``.repo`` file should exist.
            Defaults to True.
        baseurl (str | None, optional): The repository's ``baseurl``; required
            when ``present=True``. Defaults to None.
        description (str | None, optional): The ``name=`` line. Defaults to
            None, using ``src``.
        enabled (bool, optional): Whether the repo is ``enabled=1`` (else
            ``enabled=0``). Defaults to True.
        gpgcheck (bool, optional): Whether to set ``gpgcheck=1`` (else
            ``gpgcheck=0``). Defaults to True.
        gpgkey (str | None, optional): The ``gpgkey=`` value; each
            whitespace-separated token must be a URL or an absolute path.
            Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated dnf repository file commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/dnf/dnf_repo.py``.
    """

    # pyinfra ``dnf.repo`` parity.
    #
    # The ``present`` case constructs the file content and writes it only when
    # it differs from what is already there (``DnfRepoFile``), so re-running is
    # a no-op; ``present=False`` removes the file when it exists. The write
    # yields *two* descriptors from the one request -- a
    # ``DnfRepoUploadCommand`` transfer spooling the generated text to a
    # ``/tmp`` path over scp, then a ``DnfRepoInstallCommand`` running the
    # elevated install: create the directory, copy the file, chmod it ``0644``
    # (pyinfra's fixed mode) and remove the spool (the ``apt.sources_file``
    # pattern, replacing pyinfra's ``files.put`` delegation -- reemote5's
    # transfers are unprivileged scp and namespaces never reuse each other's
    # operations). Writing needs root -- ``/etc/yum.repos.d`` is root-owned --
    # so flag the request ``sudo=True`` (or ``su=True``); the transfer stays
    # unprivileged regardless.
    #
    # pyinfra's URL ``src`` -- downloading a remote ``.repo`` file -- is
    # deliberately dropped: reemote5 has no download operation, so a ``src``
    # with a URL scheme raises ``ValueError``. So does a ``present`` request
    # with no ``baseurl`` (there is nothing to construct the file from) and a
    # ``gpgkey`` token that is neither a URL nor an absolute path. ``**kwargs``
    # absorbs pyinfra's global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    if urlparse(src).scheme:
        raise ValueError(
            "dnf.repo does not support a URL src (reemote5 has no download "
            f"operation); pass a repository name and baseurl instead, got: {src}"
        )

    path = f"{_REPO_DIRECTORY}{src}.repo"
    current = await ctx.facts.get(DnfRepoFile, path)

    if not present:
        if current is None:
            ctx.reason = f"repo file already absent ({path})"
        else:
            ctx.changed = True
            ctx.reason = f"repo file {path} present, will remove"
            yield DnfRepoRemoveCommand(path=path)
        return

    if baseurl is None:
        raise ValueError(
            "dnf.repo requires baseurl when present=True and src is a "
            "repository name (there is otherwise nothing to write)"
        )

    if gpgkey:
        for token in gpgkey.split():
            if not urlparse(token).scheme and not token.startswith("/"):
                raise ValueError(
                    f"gpgkey must be a valid URL or an absolute path, got: {token}"
                )

    lines = [
        f"[{src}]",
        f"name={description or src}",
        f"baseurl={baseurl}",
        f"enabled={1 if enabled else 0}",
        f"gpgcheck={1 if gpgcheck else 0}",
    ]
    if gpgkey:
        lines.append(f"gpgkey={gpgkey}")
    lines.append("")  # trailing newline, matching pyinfra's constructed file
    content = "\n".join(lines)

    if current == content:
        ctx.reason = f"repo file already up to date ({path})"
    else:
        ctx.changed = True
        ctx.reason = f"repo file {path} missing or differs from the desired content"
        yield DnfRepoUploadCommand(content=content, dest=_REPO_SPOOL_PATH)
        yield DnfRepoInstallCommand(spool=_REPO_SPOOL_PATH, dest=path)


# --- dnf.rpm -----------------------------------------------------------------


@dataclass(frozen=True)
class RpmInstallCommand(Command):
    """``rpm -i <src>``: install a ``.rpm`` file already on the target host.

    ``src`` is shell-quoted. Unlike pyinfra's ``ensure_rpm`` -- which, for a
    file it could not read at plan time (a URL downloaded mid-deploy), emitted
    a defensive ``rpm -q \\`rpm -qp <src>\\` ... || rpm -i <src>`` runtime check
    -- reemote5 always installs with a plain ``rpm -i``: it re-materializes the
    operation at apply time against a fresh fact cache, so a ``.rpm`` created
    earlier in the same deploy (e.g. by ``files.put``) is read then, and the
    installed-version idempotence check applies normally. A plain command also
    keeps elevation correct -- ``sudo rpm -i <src>`` elevates the whole install,
    whereas a ``sudo``-prefixed ``|| ...`` chain would leave its fallback arm
    unprivileged.
    """

    src: str

    def shell(self) -> str:
        return f"rpm -i {shlex.quote(self.src)}"


async def rpm(
    ctx: OpContext,
    src: str,
    present: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Add/remove a ``.rpm`` file package.

    Args:
        ctx (OpContext): Execution context for the operation.
        src (str): Path to the ``.rpm`` file on the *target host*.
        present (bool, optional): Whether the package should be installed.
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated dnf rpm package commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/dnf/dnf_rpm.py``.
    """

    # pyinfra ``dnf.rpm`` parity.
    #
    # ``src`` names a ``.rpm`` that already exists on the target host (upload
    # one from the controller with ``files.put`` first). The ``RpmPackage``
    # fact reads the file's ``name``/``version``, then reads the installed
    # package of that name; the package counts as present only when the
    # installed version matches the file's, so ``present=True`` installs
    # (``rpm -i``) when it does not and is otherwise a no-op, while
    # ``present=False`` removes it with ``dnf remove -y <name>`` only when that
    # exact version is installed. Installing needs root, so flag the request
    # ``sudo=True`` (or ``su=True``).
    #
    # pyinfra's URL ``src`` -- downloading a remote ``.rpm`` mid-deploy via
    # ``files.download`` -- is deliberately dropped: reemote5 has no download
    # operation, so a ``src`` with a URL scheme raises ``ValueError`` (mirroring
    # ``dnf.repo``). Because removal reads the package name from the file, a
    # ``present=False`` request whose ``.rpm`` is absent from the host cannot
    # resolve a name and is a no-op -- the same limitation pyinfra documents for
    # an un-downloaded URL source. ``**kwargs`` absorbs pyinfra's global
    # arguments (``op()`` already strips ``name``/``sudo``/``su``).
    if urlparse(src).scheme:
        raise ValueError(
            "dnf.rpm does not support a URL src (reemote5 has no download "
            f"operation); put the .rpm on the host first, got: {src}"
        )

    info = await ctx.facts.get(RpmPackage, src)
    # ``installed_name`` is set only when the file's package is installed at the
    # file's exact version, so it doubles as pyinfra's ``exists`` flag and
    # carries the name the removal needs.
    installed_name: str | None = None
    if info is not None:
        current = await ctx.facts.get(RpmPackage, info["name"])
        if current is not None and current["version"] == info["version"]:
            installed_name = info["name"]

    if present:
        if installed_name is not None:
            ctx.reason = f"rpm {src} is installed"
        else:
            ctx.changed = True
            ctx.reason = f"rpm {src} not installed, will install"
            yield RpmInstallCommand(src=src)
    else:
        if installed_name is not None:
            ctx.changed = True
            ctx.reason = f"rpm {src} installed, will remove"
            yield DnfRemoveCommand(packages=(installed_name,))
        else:
            ctx.reason = f"rpm {src} is not installed"


# --- dnf.key -----------------------------------------------------------------

#: Where ``dnf.key``'s transfer spools the key stream on the target host before
#: the elevated ``rpm --import``. A fixed path under world-writable ``/tmp`` so
#: the *unprivileged* scp can write it, and a constant so the value is identical
#: across the prepare and apply phases (a per-materialization temp name would
#: diverge and trip the determinism guard). ``DnfKeyImportCommand`` removes it
#: again after importing.
_KEY_IMPORT_PATH = "/tmp/reemote5-dnf-key.gpg"


@dataclass(frozen=True)
class DnfKeyUploadCommand(TransferCommand):
    """Spool the GPG key stream to a ``/tmp`` path on the target host.

    The first half of ``dnf.key`` and a *non-shell* descriptor: it copies the
    IO-like key content over the connection's scp channel via
    ``Host.upload_file`` rather than running shell text, so it has no
    ``shell()`` and its loggable form is the ``__str__`` rendering
    ``command_shell`` falls back to. The copy rides as the login user --
    transfers are unprivileged, so the request's ``sudo`` does not apply to it
    (and ``command_shell`` renders it without the prefix) -- which is why the
    spool path is world-writable ``/tmp`` rather than a root-owned directory.
    ``src`` is the IO-like object itself (never a path): ``OperationRequest``
    compares it by identity, so a deploy must pass the *same* stream object in
    every phase -- define it once (e.g. at module level), not inside the
    generator.
    """

    src: IO[Any]  # IO-like GPG key content on the control machine
    dest: str  # spool path on the target host

    def __str__(self) -> str:
        return f"scp {getattr(self.src, 'name', '<stream>')} -> {self.dest}"

    async def transfer(self, host: Host) -> None:
        await host.upload_file(self.src, self.dest)


@dataclass(frozen=True)
class DnfKeyImportCommand(Command):
    """``rpm --import`` the spooled key, then remove the spool file.

    The second half of ``dnf.key`` and the elevated one: importing into the
    system rpm keyring needs root, so the request is flagged ``sudo=True`` and
    the import-and-remove is wrapped in ``sh -c``. A bare ``sudo rpm --import
    <path> && rm -f <path>`` would elevate only the import and leave the ``rm``
    running as the login user, whereas ``sudo sh -c '...'`` runs both steps as
    root, which can always remove the login-user spool file. The path is
    shell-quoted.
    """

    path: str

    def shell(self) -> str:
        quoted = shlex.quote(self.path)
        inner = f"rpm --import {quoted} && rm -f {quoted}"
        return f"sh -c {shlex.quote(inner)}"


async def key(ctx: OpContext, src: IO[Any], **kwargs: Any) -> AsyncGenerator[Command]:
    """Import a GPG key into the target host's rpm keyring.

    Args:
        ctx (OpContext): Execution context for the operation.
        src (IO[Any]): The key content as a seekable IO-like object on the
            control machine (an ``io.BytesIO``/``io.StringIO``, a file opened
            with ``open()``, ...), rewound to position 0 before it is read.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The key transfer and import commands, always (the operation
            is stateless).

    **Raises:**

    ```python
    ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/dnf/dnf_key.py``.
    """

    # pyinfra ``dnf.key`` parity.
    #
    # Like pyinfra's ``dnf.key`` this is **non-idempotent**: it always imports,
    # so it always reports a change and yields its commands on every run (rpm
    # itself deduplicates an already-imported key). It yields *two* descriptors
    # from the one request -- a ``DnfKeyUploadCommand`` transfer that spools the
    # stream to a ``/tmp`` path over scp, then a ``DnfKeyImportCommand`` running
    # the elevated ``rpm --import`` on that spool file and removing it. Because
    # the import needs root, flag the request ``sudo=True`` (or ``su=True``);
    # the transfer stays unprivileged regardless (transfers ride the connection
    # as the login user), so the two commands of one request run at different
    # privilege levels -- the driver dispatches the transfer to scp and elevates
    # only the shell import.
    #
    # pyinfra's ``dnf.key`` takes a ``src`` that is a *filename or URL* the target
    # host reads directly (``rpm --import <src>``). reemote5 does not support
    # controller-side file locations, so ``src`` is an IO-like *stream* of the key
    # content instead, and the bytes reach the host over the connection's scp
    # channel rather than by naming a path rpm can open; a ``str`` ``src`` raises
    # ``ValueError``. ``**kwargs`` absorbs pyinfra's global arguments (``op()``
    # already strips ``name``/``sudo``/``su``).
    if isinstance(src, str):
        # ValueError (not TypeError) keeps dnf.key consistent with dnf.repo and
        # dnf.rpm, which also reject an unusable string ``src`` with ValueError;
        # TRY004 assumes any isinstance guard should raise TypeError instead.
        raise ValueError(  # noqa: TRY004
            "dnf.key does not support a filename/URL src (reemote5 takes IO "
            "streams, not controller-side file locations); pass the key content "
            f"as an IO-like object, got: {src!r}"
        )
    ctx.changed = True
    ctx.reason = f"import GPG key from {getattr(src, 'name', '<stream>')}"
    yield DnfKeyUploadCommand(src=src, dest=_KEY_IMPORT_PATH)
    yield DnfKeyImportCommand(path=_KEY_IMPORT_PATH)


# --- dnf.module --------------------------------------------------------------


@dataclass(frozen=True)
class DnfModuleEnableCommand(Command):
    """``dnf module enable -y <module>:<stream>``: enable a module stream.

    ``module`` and ``stream`` are joined with ``:`` and shell-quoted as a
    single argument, so a stream name carrying shell metacharacters reaches
    dnf literally. Enabling a stream writes dnf's module state, so the request
    is flagged ``sudo=True`` (or ``su=True``) and the driver elevates it.
    """

    module: str
    stream: str

    def shell(self) -> str:
        return f"dnf module enable -y {shlex.quote(f'{self.module}:{self.stream}')}"


@dataclass(frozen=True)
class DnfModuleDisableCommand(Command):
    """``dnf module disable -y <module>``: disable a module (all its streams).

    ``module`` is shell-quoted. Disabling writes dnf's module state, so the
    request is flagged ``sudo=True`` (or ``su=True``) and the driver elevates it.
    """

    module: str

    def shell(self) -> str:
        return f"dnf module disable -y {shlex.quote(self.module)}"


async def module(
    ctx: OpContext,
    module: str,
    stream: str | None = None,
    enabled: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Enable/disable a dnf module stream.

    Args:
        ctx (OpContext): Execution context for the operation.
        module (str): Name of the dnf module (an application-stream family).
        stream (str | None, optional): The stream to enable; required when
            ``enabled=True``, ignored when ``enabled=False``. Defaults to None.
        enabled (bool, optional): ``True`` enables ``module:stream``; ``False``
            disables the module, so every one of its streams becomes inactive.
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated dnf module commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/dnf/dnf_module.py``.
    """

    # pyinfra ``dnf.module`` parity.
    #
    # ``enabled=True`` compares against ``DnfEnabledModules`` (a list of
    # ``DnfModule`` entries): when ``module`` is already enabled on exactly
    # ``stream`` the request is a no-op, otherwise it yields one ``dnf module
    # enable`` command -- so a ``module`` enabled on a *different* stream is
    # re-enabled onto the requested one. ``enabled=False`` compares against
    # ``DnfDisabledModules`` (also ``DnfModule`` entries): when ``module`` is
    # already disabled the request is a no-op, otherwise it yields one ``dnf
    # module disable`` command. Either way enabling
    # or disabling writes dnf's module state, so flag the request ``sudo=True``
    # (or ``su=True``).
    #
    # Like pyinfra, ``stream`` is required when ``enabled=True`` and a missing
    # one raises ``ValueError`` -- pyinfra raises its ``OperationValueError``,
    # itself a ``ValueError`` subclass, so reemote5 uses the plain ``ValueError``
    # its sibling operations (``dnf.repo``/``dnf.rpm``/``dnf.key``) already raise.
    # pyinfra's stream-switching caveat is inherited unchanged: ``dnf module
    # enable`` refuses to switch streams while packages from the active stream
    # are installed, so those must be removed (or the module ``dnf module
    # reset``) first -- this operation does not do that for you. ``**kwargs``
    # absorbs pyinfra's global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    if enabled:
        if stream is None:
            raise ValueError("stream is required when enabled=True")
        enabled_modules = await ctx.facts.get(DnfEnabledModules)
        current = next(
            (entry["stream"] for entry in enabled_modules if entry["name"] == module),
            None,
        )
        if current == stream:
            ctx.reason = f"dnf module {module}:{stream} is already enabled"
            return
        ctx.changed = True
        ctx.reason = (
            f"dnf module {module}:{stream} not enabled, will enable"
            if current is None
            else f"dnf module {module} is enabled on stream {current}, "
            f"will switch to {stream}"
        )
        yield DnfModuleEnableCommand(module=module, stream=stream)
        return

    disabled = await ctx.facts.get(DnfDisabledModules)
    if any(entry["name"] == module for entry in disabled):
        ctx.reason = f"dnf module {module} is already disabled"
        return
    ctx.changed = True
    ctx.reason = f"dnf module {module} not disabled, will disable"
    yield DnfModuleDisableCommand(module=module)


__all__ = [
    "DnfAvailableGroups",
    "DnfCleanCommand",
    "DnfDisabledModules",
    "DnfEnabledModules",
    "DnfGroup",
    "DnfGroups",
    "DnfInstallCommand",
    "DnfInstalledModules",
    "DnfKeyImportCommand",
    "DnfKeyUploadCommand",
    "DnfModule",
    "DnfModuleDisableCommand",
    "DnfModuleEnableCommand",
    "DnfPackageInfo",
    "DnfPackages",
    "DnfRemoveCommand",
    "DnfRepoFile",
    "DnfRepoInstallCommand",
    "DnfRepoList",
    "DnfRepoListEntry",
    "DnfRepoRemoveCommand",
    "DnfRepoUploadCommand",
    "DnfRepositories",
    "DnfRepository",
    "DnfUpdateCommand",
    "RpmInstallCommand",
    "RpmPackage",
    "RpmPackageInfo",
    "key",
    "module",
    "packages",
    "repo",
    "rpm",
    "update",
]
