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
# groups (shared ``DnfGroup`` entries), ``RpmPackage``, the
# name/version (``RpmPackageInfo``) of a ``.rpm`` file or an
# installed package that ``dnf.rpm`` reads to decide idempotence, and
# ``RpmGpgKeys``, the GPG keys imported into the rpm keyring (``RpmGpgKey``
# entries) that ``dnf.key``'s removal direction reads. Like every
# fact they are phase-unaware: each ``process`` coroutine just gathers the raw
# value.
#
# Operation side (design doc section 6): ``dnf.packages``, ``dnf.update``,
# ``dnf.makecache``, ``dnf.clean``, ``dnf.repo``,
# ``dnf.rpm``, ``dnf.key`` and ``dnf.module`` plus the typed command descriptors
# they yield (``DnfInstallCommand``/``DnfDowngradeCommand``/``DnfRemoveCommand``/
# ``DnfUpdateCommand``/
# ``DnfCleanCommand``/``DnfMakecacheCommand``/``DnfRepoUploadCommand``/``DnfRepoInstallCommand``/
# ``DnfRepoRemoveCommand``/``RpmInstallCommand``/
# ``DnfKeyUploadCommand``/``DnfKeyImportCommand``/``DnfKeyRemoveCommand``/
# ``DnfModuleEnableCommand``/
# ``DnfModuleDisableCommand``). Operations are
# async generators that receive an ``OpContext``, await facts, set
# ``ctx.changed``/``ctx.reason``, and yield command descriptors; they never touch
# the host directly.
#
# ``dnf.packages`` covers the usual option set (``present``, ``latest``,
# ``update``, ``clean``, ``nobest``, ``extra_install_args``,
# ``extra_uninstall_args``) with the same names and semantics, plus a
# first-class ``repos`` scope (``--disablerepo='*' --enablerepo=<id>``) on the
# install/remove it yields, a first-class ``disable_repos`` (bare
# ``--disablerepo=<id>`` per id, distinct from ``repos``' disable-all-then-
# enable-named) so the ansible-datadog role's ``disablerepo: appstream`` does
# not have to be smuggled through ``extra_install_args``, and an
# ``update_cache`` flag that yields a scoped ``DnfMakecacheCommand`` ahead of
# the install/remove work -- the role's ``update_cache: true`` -- so a
# metadata refresh is first-class rather than a separate ``dnf.makecache``
# request. A pinned ``<pkg>=<version>`` is present only when
# that *exact* version is installed (the ``DnfPackages`` fact already reports
# each name's versions -- the ``apt.packages`` precedent -- so the comparison
# is version-aware rather than name-only), and ``allow_downgrade`` renders
# ``dnf downgrade --allowerasing`` for a pinned package whose name is already
# installed at a different version, since ``dnf install`` alone will not cross
# from a newer installed version to an older requested one. ``dnf.repo``
# constructs a repo file for the manual-construction case (``present``,
# ``baseurl``, ``description``, ``enabled``, ``gpgcheck``, ``gpgkey``) and
# extends it with the ansible-datadog role's ``yum_repository`` fields
# (``includepkgs``, ``repo_gpgcheck``, ``proxy``, ``proxy_username``,
# ``proxy_password``); a URL source is dropped, since reemote has no
# download operation. ``dnf.makecache`` and ``dnf.clean`` are reemote
# originals: stateless metadata refresh/cache removal,
# optionally scoped to named repos with ``--disablerepo='*' --enablerepo=<id>``
# -- the role's scoped ``makecache``/``clean metadata`` steps, which
# ``dnf.packages(update=True)`` (a full system upgrade) and ``clean=True``
# (``dnf clean all``) cannot express.
# ``dnf.rpm`` (``src``, ``present``) installs a ``.rpm``
# file already on the target host; a URL source -- which downloads the
# file mid-deploy -- is dropped for the same reason, so a URL ``src`` raises
# ``ValueError``. ``dnf.key`` is non-idempotent and
# takes the GPG key content as an IO-like *stream* -- reemote has no
# controller-side file locations -- spooling it to a ``/tmp`` path over scp and
# running an elevated ``rpm --import`` on it, so a ``str`` ``src`` raises
# ``ValueError``. ``dnf.key``'s ``present=False`` direction is a reemote
# extension (import alone would not cover removal), mirroring
# Ansible's ``rpm_key state=absent``: it reads ``RpmGpgKeys`` and erases the
# named key's ``gpg-pubkey`` pseudo-package with an elevated ``rpm -e`` only
# when the key is present. ``dnf.update`` is non-idempotent:
# it reads no fact and always runs ``dnf update -y`` over every package.
# """

import hashlib
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

    # The installed-package listing, with one deliberate extension:
    # a names-only query drops versions, while the query format here also
    # prints each package's ``VERSION-RELEASE`` (the same version string
    # ``RpmPackage`` reports), so every installed version is kept in the
    # entry's ``versions`` list rather than dropped. Names are
    # de-duplicated and sorted, versions grouped per name -- the apt-side
    # convention (``AptPackages``' sorted version lists) rather than
    # a runtime set -- so reads stay deterministic and
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
    for f in /etc/dnf.conf /etc/dnf.repos.d/*.repo /etc/yum.repos.d/*.repo; do [ -f "$f" ] && { printf "#reemote-filename: %s\\n" "$f"; cat "$f"; }; done || true
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

    # Every ``[section]`` of the dnf
    # config and ``.repo`` files becomes one ``DnfRepository`` entry with
    # ``repoid``/``name`` (both defaulting to the section id), the source
    # ``filename``, and each remaining ``key=value`` option in ``options``.

    #: Marker prefixing each cat'd file in ``COMMAND``'s output, naming the
    #: source file so ``parse`` can attribute a repository to its ``.repo``
    #: file.
    FILENAME_MARKER = "#reemote-filename:"

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
    test -e <path> && { printf '#reemote-repofile\\n'; cat -- <path>; }
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
    MARKER = "#reemote-repofile"

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

    # Every row of the
    # ``dnf module list --enabled`` table carrying an ``[e]`` flag becomes a
    # ``DnfModule`` entry naming the module and its enabled stream; headers,
    # blank lines and the trailing hint line are ignored.

    #: Shell used to list the enabled modules over SSH.
    COMMAND = "dnf module list --enabled"

    #: Matches the ``[e]`` enabled flag on a stream but not the ``[e]nabled``
    #: word in the table's trailing ``Hint:`` line.
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

    # Every row of the
    # ``dnf module list --disabled`` table carrying an ``[x]`` flag becomes a
    # ``DnfModule`` entry naming the module and the flagged stream; a module
    # disabled across several streams contributes one entry per row, and the
    # entries are de-duplicated and sorted by name, then stream. Headers,
    # blank lines and the trailing hint line are ignored.

    #: Shell used to list the disabled modules over SSH.
    COMMAND = "dnf module list --disabled"

    #: Matches the ``[x]`` disabled flag on a stream but not the ``[x]disabled``
    #: word in the table's trailing ``Hint:`` line.
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
    a runtime set).
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

    # Fills a gap: there is no other way to ask which package
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

    # Given a package *name* or a ``.rpm``
    # file *path*, returns a ``RpmPackageInfo`` entry (``version`` is
    # rpm's ``VERSION-RELEASE``), or None when the name is not installed and the
    # path is not an existing file. ``dnf.rpm`` reads it twice -- once for the
    # ``.rpm`` file, once for the installed package of that file's name -- and
    # compares the two versions to decide whether the file's package is already
    # installed at that version.

    #: rpm query format printing ``NAME VERSION-RELEASE`` on one line.
    QUERY_FORMAT = "%{NAME} %{VERSION}-%{RELEASE}\\n"

    #: Matches the ``NAME VERSION-RELEASE`` line ``QUERY_FORMAT`` prints.
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


class RpmGpgKey(TypedDict):
    """
    Attributes:
        keyid: The key's short id -- the last 8 hex digits of its GPG key
            id, lowercased exactly as rpm stores it in the pseudo-package's
            ``VERSION``.
        release: The pseudo-package's ``RELEASE`` -- the hex-encoded key
            creation timestamp rpm recorded when the key was imported.
        summary: The pseudo-package's ``SUMMARY`` -- rpm's ``gpg(<uid>)``
            description of the imported key.
    """

    # One imported key (``RpmGpgKeys``' entry). A TypedDict rather than a
    # class instance, like ``RpmPackageInfo``: entries are ordinary dicts, so
    # equality against a literal dict works and ``json.dumps`` serializes
    # them directly. The bare singular slot is free (the fact is the plural
    # ``RpmGpgKeys``), so no ``Info`` suffix is needed.

    keyid: str
    release: str
    summary: str


class RpmGpgKeys:
    """The GPG keys imported into the rpm keyring, as ``RpmGpgKey`` dicts.

    **Commands:**

    ```bash
    rpm -qa gpg-pubkey --queryformat '%{VERSION} %{RELEASE} %{SUMMARY}\\n'
    ```

    **Returns:**

    ```python
    list[RpmGpgKey]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/dnf/rpm_gpg_keys.py``.
    """

    # A reemote-original fact (there is no other rpm-keyring fact): it fills the
    # observation gap ``dnf.key``'s ``present=False`` direction needs.
    # ``rpm --import`` stores each key in the rpm database as a
    # ``gpg-pubkey-<keyid>-<release>`` pseudo-package -- invisible to
    # ``dnf list installed`` and to the ``gpg`` namespace's keyring facts --
    # so the query selects that package name and prints its VERSION (the
    # lowercased short key id), RELEASE (hex creation timestamp) and SUMMARY
    # (``gpg(<uid>)``). Entries are de-duplicated and sorted by keyid then
    # release so reads stay deterministic and JSON-printable for the
    # ``gather_fact`` example scripts. Ansible's ``rpm_key state=absent``
    # consults the same pseudo-packages.

    #: Shell listing the imported keys' id, release and summary over SSH.
    COMMAND = "rpm -qa gpg-pubkey --queryformat '%{VERSION} %{RELEASE} %{SUMMARY}\n'"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[RpmGpgKey]:
        """Parse ``rpm -qa gpg-pubkey`` rows into sorted, unique entries.

        Each non-blank line carries the key id, the release and the summary,
        whitespace-separated; the summary may contain spaces, so the line
        splits into at most three tokens. Key ids are lowercased, duplicate
        rows collapse, and the entries sort by keyid then release. Pure and
        testable.
        """
        summaries: dict[tuple[str, str], str] = {}
        for line in output:
            tokens = line.split(None, 2)
            if len(tokens) < 2:
                continue
            keyid, release = tokens[0].lower(), tokens[1]
            summary = tokens[2] if len(tokens) == 3 else ""
            summaries.setdefault((keyid, release), summary)
        return [
            {"keyid": keyid, "release": release, "summary": summary}
            for (keyid, release), summary in sorted(summaries.items())
        ]

    @classmethod
    async def process(cls, host: Host) -> list[RpmGpgKey]:
        rpm_gpg_keys = getattr(host, "rpm_gpg_keys", None)
        if rpm_gpg_keys is not None:
            # SimulatedHost seam: the entry list the mutators edit, sorted
            # and copied so callers never mutate simulated state.
            return [
                dict(entry)
                for entry in sorted(
                    rpm_gpg_keys,
                    key=lambda item: (item["keyid"], item["release"]),
                )
            ]
        # Real transport: query the rpm database over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


# --- Operations --------------------------------------------------------------


def _split_version(package: str) -> tuple[str, str | None]:
    """Split ``pkg=version`` into ``(name, version)``; version is None if absent.

    Splitting on the *last* ``=`` (``rsplit``) keeps a version string
    carrying ``=`` intact -- the same split ``apt.packages`` uses.
    """
    name, sep, version = package.rpartition("=")
    return (name, version) if sep else (package, None)


def _dnf_package_spec(package: str) -> str:
    """Render a ``pkg=version`` pin as dnf's native ``pkg-version`` NEVRA spec.

    reemote spells a version pin ``pkg=version`` across every package module
    (the ``apt.packages`` convention, since apt-get's own CLI syntax is
    ``pkg=version``); dnf's CLI has no ``=`` form and takes
    ``name-[epoch:]version[-release]`` instead, so the pin is rewritten to a
    dash-joined spec before it reaches the shell. An unpinned name passes
    through unchanged.
    """
    name, version = _split_version(package)
    return package if version is None else f"{name}-{version}"


def _repo_scope_args(
    repos: tuple[str, ...], disable_repos: tuple[str, ...] = ()
) -> str:
    """Render the repo-scoping flags shared by the package and cache descriptors.

    Two orthogonal knobs, either or both of which may be empty:

    * ``repos`` is enable-only-named scoping -- a non-empty tuple renders
      ``--disablerepo='*'`` followed by one ``--enablerepo=<id>`` per id, the
      ansible-datadog role's ``--disablerepo="*" --enablerepo=datadog`` shape,
      so the command applies to *only* those repos.
    * ``disable_repos`` is bare per-id disabling -- a non-empty tuple renders
      one ``--disablerepo=<id>`` per id and nothing else, so the command
      applies to every configured repo *except* those ids (the role's
      ``disablerepo: appstream`` shape). Rendering the two together is
      allowed but rarely useful: dnf processes the flags in order, so a name
      appearing in both lists ends up disabled.

    Empty tuples on both sides render nothing (the command applies to every
    configured repo). Each flag is shell-quoted whole, so the remote shell
    never globs the ``*``.
    """
    parts: list[str] = []
    if repos:
        parts.append(shlex.quote("--disablerepo=*"))
        parts.extend(shlex.quote(f"--enablerepo={repo}") for repo in repos)
    parts.extend(shlex.quote(f"--disablerepo={repo}") for repo in disable_repos)
    if not parts:
        return ""
    return " " + " ".join(parts)


def _normalize_repos(repos: str | list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    """Normalize a repos argument: a bare string becomes a one-tuple, None an
    empty tuple (every repo), anything else a tuple -- the ``dnf.packages``
    normalization shape."""
    if repos is None:
        return ()
    if isinstance(repos, str):
        return (repos,)
    return tuple(repos)


@dataclass(frozen=True)
class DnfInstallCommand(Command):
    packages: tuple[str, ...]
    nobest: bool = False
    extra_args: str | None = None
    repos: tuple[str, ...] = ()
    disable_repos: tuple[str, ...] = ()

    def shell(self) -> str:
        parts = ["dnf", "install", "-y"]
        if self.nobest:
            parts.append("--nobest")
        if self.extra_args:
            parts.append(self.extra_args)
        scope = _repo_scope_args(self.repos, self.disable_repos).strip()
        if scope:
            parts.append(scope)
        parts.extend(shlex.quote(_dnf_package_spec(package)) for package in self.packages)
        return " ".join(parts)


@dataclass(frozen=True)
class DnfDowngradeCommand(Command):
    """``dnf downgrade --allowerasing <packages>``: downgrade already-installed
    packages to an explicitly requested older version.

    ``dnf install`` will not cross from a newer installed version to an older
    requested one; ``dnf downgrade`` is dnf's dedicated verb for that (there is
    no ``--allow-downgrades`` install flag equivalent, unlike apt), and
    ``--allowerasing`` lets it drop packages whose dependencies would
    otherwise block the downgrade. ``dnf.packages(allow_downgrade=True)``
    yields this for a pinned ``<pkg>=<version>`` whose *name* is already
    installed at a different version; a name that is not installed at all
    still goes through ``DnfInstallCommand``, since there is nothing to
    downgrade from. ``nobest``/``extra_args``/``repos`` render exactly as they
    do on the install descriptor.
    """

    packages: tuple[str, ...]
    nobest: bool = False
    extra_args: str | None = None
    repos: tuple[str, ...] = ()
    disable_repos: tuple[str, ...] = ()

    def shell(self) -> str:
        parts = ["dnf", "downgrade", "-y", "--allowerasing"]
        if self.nobest:
            parts.append("--nobest")
        if self.extra_args:
            parts.append(self.extra_args)
        scope = _repo_scope_args(self.repos, self.disable_repos).strip()
        if scope:
            parts.append(scope)
        parts.extend(shlex.quote(_dnf_package_spec(package)) for package in self.packages)
        return " ".join(parts)


@dataclass(frozen=True)
class DnfRemoveCommand(Command):
    packages: tuple[str, ...]
    extra_args: str | None = None
    repos: tuple[str, ...] = ()
    disable_repos: tuple[str, ...] = ()

    def shell(self) -> str:
        parts = ["dnf", "remove", "-y"]
        if self.extra_args:
            parts.append(self.extra_args)
        scope = _repo_scope_args(self.repos, self.disable_repos).strip()
        if scope:
            parts.append(scope)
        parts.extend(shlex.quote(_dnf_package_spec(package)) for package in self.packages)
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
    """``dnf clean <what>``: ``all`` by default (the ``dnf.packages`` ``clean``
    option), or one cache kind for ``dnf.clean`` -- optionally scoped to
    ``repos`` with ``--disablerepo='*' --enablerepo=<id>`` flags."""

    what: str = "all"
    repos: tuple[str, ...] = ()

    def shell(self) -> str:
        return f"dnf clean {self.what}{_repo_scope_args(self.repos)}"


async def packages(
    ctx: OpContext,
    packages: str | list[str] | tuple[str, ...] | None = None,
    present: bool = True,
    latest: bool = False,
    update: bool = False,
    clean: bool = False,
    update_cache: bool = False,
    nobest: bool = False,
    allow_downgrade: bool = False,
    extra_install_args: str | None = None,
    extra_uninstall_args: str | None = None,
    repos: str | list[str] | tuple[str, ...] | None = None,
    disable_repos: str | list[str] | tuple[str, ...] | None = None,
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
        update_cache (bool, optional): Refresh repository metadata with a
            scoped ``dnf -y makecache`` before the install/remove work --
            Ansible's ``update_cache: true`` spelling, and a first-class
            alternative to a standalone ``dnf.makecache`` request. Inherits
            the same ``repos``/``disable_repos`` scoping as the transaction
            that follows, is **non-idempotent** (always reports a change and
            always runs; dnf itself is cheap on a warm cache), and yields
            ahead of ``update``/install/remove so the metadata is fresh
            before dnf resolves anything. Defaults to False.
        nobest (bool, optional): Add the ``--nobest`` option to the install
            command. Defaults to False.
        allow_downgrade (bool, optional): Allow a pinned ``<pkg>=<version>``
            whose name is already installed at a *different* version to be
            downgraded to the requested one, rendering ``dnf downgrade
            --allowerasing`` instead of ``dnf install`` (dnf's own verb for
            crossing from a newer installed version to an older requested one,
            since ``dnf install`` will not do it and has no equivalent flag).
            Without this, a version mismatch is treated as "not present" and
            routed through ``DnfInstallCommand`` exactly like a missing name,
            leaving dnf's own default behavior (which refuses the downgrade)
            to decide the outcome -- the ``apt.packages(allow_downgrades=...)``
            precedent. Defaults to False.
        extra_install_args (str | None, optional): Additional arguments to the
            dnf install command. Defaults to None.
        extra_uninstall_args (str | None, optional): Additional arguments to
            the dnf remove command. Defaults to None.
        repos (str | list[str] | tuple[str, ...] | None, optional): Repository
            ids to scope the install/remove to (``--disablerepo='*'`` plus one
            ``--enablerepo`` per id), so packages resolve only from the named
            repos. Prefer tuples when building requests via ``op()``, since
            ``OperationRequest`` kwargs must stay hashable. Defaults to None
            (every enabled repo).
        disable_repos (str | list[str] | tuple[str, ...] | None, optional):
            Repository ids to disable *bare* on the install/downgrade/remove
            this operation yields (one ``--disablerepo=<id>`` per id, no
            ``--disablerepo='*'`` and no ``--enablerepo``), so packages
            resolve from every configured repo *except* the named ones --
            Ansible's ``disablerepo: <one>`` spelling and a first-class
            alternative to smuggling ``--disablerepo=appstream`` through
            ``extra_install_args``. Distinct from ``repos`` (which means
            disable-all-then-enable-named); passing both is allowed but
            rarely useful, since dnf processes the flags in order and a name
            appearing in both ends up disabled. Prefer tuples when building
            requests via ``op()``, since ``OperationRequest`` kwargs must
            stay hashable. Defaults to None (no repo disabled).
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

    # ``dnf.packages``: install, remove and update packages.
    #
    # ``clean``, ``update`` and ``update_cache`` are stateless (always yield,
    # like the other non-idempotent operations), in that order -- clean wipes
    # the cache, ``update_cache`` refreshes it, then ``update`` performs the
    # full system upgrade -- so a subsequent install/remove resolves against
    # whatever metadata the caller asked for. The ``DnfPackages`` fact reports
    # each name's installed versions, so the comparison is version-aware (the
    # ``apt.packages`` precedent): a pinned ``<pkg>=<version>`` is present
    # only when that *exact* version is installed, and ``latest`` still only
    # upgrades unpinned names (every installed, unpinned package is upgraded
    # without checking whether an upgrade actually exists, the same blind
    # behavior as before). ``allow_downgrade`` decides what a version mismatch
    # does when ``present=True``: with it, a pinned package whose name is
    # installed at a different version routes to ``DnfDowngradeCommand``
    # (``dnf downgrade --allowerasing``) instead of ``DnfInstallCommand``,
    # since ``dnf install`` alone refuses to cross that boundary; without it,
    # the mismatch is treated exactly like a missing name and left for dnf's
    # own default behavior to reject. ``repos`` scopes the
    # install/downgrade/remove this operation yields to named repos
    # (``--disablerepo='*' --enablerepo=<id>``), reusing the ``_repo_scope_args``
    # the cache descriptors render -- a first-class alternative to smuggling the
    # same flags through ``extra_install_args``/``extra_uninstall_args``.
    # ``disable_repos`` is the *bare* per-id counterpart (one
    # ``--disablerepo=<id>`` per name, no ``--disablerepo='*'``), so Ansible's
    # ``disablerepo: appstream`` -- "every repo except this one" -- is
    # first-class rather than smuggled through ``extra_install_args``; it
    # also threads into the ``update_cache`` makecache so the refresh skips
    # the same repos the transaction does. ``kwargs`` absorbs any further
    # keyword arguments (any further global arguments are meta-parameters
    # handled by ``op()`` here).
    reasons: list[str] = []

    if clean:
        ctx.changed = True
        reasons.append("dnf clean")
        yield DnfCleanCommand()

    # ``update_cache`` is scoped by the same ``repos``/``disable_repos`` as
    # the transaction that follows, so the refresh and the install agree on
    # which repos are in play. Yielded ahead of ``update`` so a full system
    # upgrade sees fresh metadata too.
    scoped = _normalize_repos(repos)
    scoped_disable = _normalize_repos(disable_repos)
    if update_cache:
        ctx.changed = True
        reasons.append("dnf makecache")
        yield DnfMakecacheCommand(repos=scoped, disable_repos=scoped_disable)

    if update:
        ctx.changed = True
        reasons.append("dnf update")
        yield DnfUpdateCommand()

    # Normalize: accept a bare string, a list, or None.
    if packages is None:
        requested: tuple[str, ...] = ()
    elif isinstance(packages, str):
        requested = (packages,)
    else:
        requested = tuple(packages)

    if requested:
        installed = await ctx.facts.get(DnfPackages)
        versions_by_name = {entry["name"]: entry["versions"] for entry in installed}

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
                # ``allow_downgrade``: a pinned package whose *name* is already
                # installed (but at a different version, so ``is_installed``
                # returned False) needs dnf's dedicated downgrade verb; a name
                # that is not installed at all has nothing to downgrade from
                # and stays a plain install.
                if allow_downgrade:
                    to_downgrade = tuple(
                        pkg
                        for pkg in to_install
                        if _split_version(pkg)[0] in versions_by_name
                    )
                    to_fresh_install = tuple(
                        pkg
                        for pkg in to_install
                        if _split_version(pkg)[0] not in versions_by_name
                    )
                else:
                    to_downgrade = ()
                    to_fresh_install = to_install
                if to_fresh_install:
                    reasons.append(f"{len(to_fresh_install)} package(s) to install")
                    yield DnfInstallCommand(
                        packages=to_fresh_install,
                        nobest=nobest,
                        extra_args=extra_install_args,
                        repos=scoped,
                        disable_repos=scoped_disable,
                    )
                if to_downgrade:
                    reasons.append(f"{len(to_downgrade)} package(s) to downgrade")
                    yield DnfDowngradeCommand(
                        packages=to_downgrade,
                        nobest=nobest,
                        extra_args=extra_install_args,
                        repos=scoped,
                        disable_repos=scoped_disable,
                    )
            if to_upgrade:
                ctx.changed = True
                reasons.append(f"{len(to_upgrade)} package(s) to upgrade")
                yield DnfUpdateCommand(packages=to_upgrade)
            if not to_install and not to_upgrade:
                reasons.append("all packages present")
        else:
            to_remove = tuple(pkg for pkg in requested if is_installed(pkg))
            if to_remove:
                ctx.changed = True
                reasons.append(f"{len(to_remove)} package(s) to remove")
                yield DnfRemoveCommand(
                    packages=to_remove,
                    extra_args=extra_uninstall_args,
                    repos=scoped,
                    disable_repos=scoped_disable,
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

    # ``dnf.update``: upgrade every package.
    #
    # This is **non-idempotent**: it reads no
    # fact, so it always reports a change and yields one bare
    # ``DnfUpdateCommand`` (rendering ``dnf update -y``) on every run. There is
    # no cheap way to tell whether any package actually has a pending upgrade,
    # so dnf is simply invoked and is itself a no-op when the system is already
    # up to date. Updating the whole system needs root, so flag the request
    # ``sudo=True`` (or ``su=True``). ``**kwargs`` absorbs any further global
    # arguments (``op()`` already strips ``name``/``sudo``/``su``).
    ctx.changed = True
    ctx.reason = "dnf update"
    yield DnfUpdateCommand()


# --- dnf.repo ----------------------------------------------------------------

#: Directory dnf/yum reads ``.repo`` files from (the usual
#: ``repo_directory`` default).
_REPO_DIRECTORY = "/etc/yum.repos.d/"


def _repo_spool_path(dest: str) -> str:
    """The deterministic ``/tmp`` spool *file* path ``dnf.repo`` uploads to.

    Derived from the managed ``.repo`` file's sha1 (``apt.repo``'s
    ``_repo_spool_path``, itself ``files.install``'s ``_install_spool_path``
    pattern) so two repository writes naming different files in one deploy
    never clobber each other's spool, while the same ``dest`` always yields
    the same name in both phases -- the request sequence stays deterministic.
    The generated text is spooled to a fixed ``repo`` file inside that
    directory rather than at the top level of ``/tmp``, and the elevated
    install ``rm -rf``s the whole directory afterwards, so no leftover lands
    in ``/tmp``. The name avoids *collisions*, not a hostile local user: it
    is predictable, so on a multi-tenant host the ``/tmp`` spool is still a
    symlink-race exposure (the trade-off every spool-and-install operation
    here makes).
    """
    digest = hashlib.sha1(dest.encode()).hexdigest()[:16]
    return f"/tmp/reemote-dnf-repo-{digest}/repo"


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
        # scp does not create parent directories, and the spool now lives
        # inside a sha1-derived /tmp directory rather than at the top level,
        # so mkdir -p it first. Unprivileged: /tmp is world-writable, so the
        # login user can create the directory, and a transfer never elevates.
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        await host.run_shell(f"mkdir -p {parent}")
        await host.upload_file(io.BytesIO(self.content.encode()), self.dest)


@dataclass(frozen=True)
class DnfRepoInstallCommand(Command):
    """Install the spooled ``.repo`` file, then remove the spool file.

    The second half of ``dnf.repo``'s write direction and the elevated one:
    writing under ``/etc/yum.repos.d/`` needs root, so the request is
    flagged ``sudo=True`` and the whole sequence is wrapped in ``sh -c`` --
    a bare ``sudo`` prefix would elevate only the first command of the
    ``&&`` chain and leave the cleanup running as the login user, whereas
    ``sudo sh -c '...'`` runs every step as root, which can always remove
    the login-user spool file. The sequence creates the destination
    directory, copies the spooled content over the target file, chmods it
    to ``mode`` (``0644`` by default -- the mode a ``files.put``-style
    route would use; ``dnf.repo`` tightens it to
    ``0600`` when the content carries a ``proxy_password``) and removes the
    spool file -- the ``AptSourcesFileInstallCommand`` pattern. All paths
    are shell-quoted.
    """

    spool: str  # where DnfRepoUploadCommand wrote the content
    dest: str  # the /etc/yum.repos.d/<src>.repo path
    mode: str = "0644"  # octal-digit text, as the chmod argument

    def shell(self) -> str:
        spool = shlex.quote(self.spool)
        # The spool file lives inside a sha1-derived /tmp directory; remove
        # the whole directory so no leftover lands in /tmp.
        spool_dir = shlex.quote(str(PurePosixPath(self.spool).parent))
        dest = shlex.quote(self.dest)
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        inner = (
            f"mkdir -p {parent} && "
            f"cp {spool} {dest} && "
            f"chmod {self.mode} {dest} && rm -rf {spool_dir}"
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
    includepkgs: str | None = None,
    repo_gpgcheck: bool | None = None,
    proxy: str | None = None,
    proxy_username: str | None = None,
    proxy_password: str | None = None,
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
            ``gpgcheck=0``) -- signature checking of the repo's *packages*.
            Defaults to True.
        gpgkey (str | None, optional): The ``gpgkey=`` value; each
            whitespace-separated token must be a URL or an absolute path.
            Defaults to None.
        includepkgs (str | None, optional): The ``includepkgs=`` value -- a
            space-separated list of package names/globs the repo may supply
            (e.g. ``datadog-agent``), so it cannot provide system packages.
            Defaults to None (the line is omitted).
        repo_gpgcheck (bool | None, optional): The ``repo_gpgcheck=`` value --
            signature checking of the repo's *metadata*, distinct from
            ``gpgcheck``. Defaults to None (the line is omitted, leaving dnf's
            own default of off).
        proxy (str | None, optional): The ``proxy=`` value -- a proxy URL, or
            ``_none_`` to disable a proxy inherited from ``dnf.conf``.
            Defaults to None (the line is omitted).
        proxy_username (str | None, optional): The ``proxy_username=`` value.
            Defaults to None (the line is omitted).
        proxy_password (str | None, optional): The ``proxy_password=`` value.
            When set, the file is installed ``0600`` instead of ``0644`` so
            the password is not world-readable. Defaults to None (the line
            is omitted).
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

    # ``dnf.repo``: add, remove or update a repository file.
    #
    # The ``present`` case constructs the file content and writes it only when
    # it differs from what is already there (``DnfRepoFile``), so re-running is
    # a no-op; ``present=False`` removes the file when it exists. The write
    # yields *two* descriptors from the one request -- a
    # ``DnfRepoUploadCommand`` transfer spooling the generated text to a
    # ``/tmp`` path over scp, then a ``DnfRepoInstallCommand`` running the
    # elevated install: create the directory, copy the file, chmod it
    # (``0644``, the fixed default mode, or ``0600`` with a ``proxy_password``)
    # and remove the spool (the ``apt.sources_file``
    # pattern, replacing a ``files.put`` delegation -- reemote's
    # transfers are unprivileged scp and namespaces never reuse each other's
    # operations). Writing needs root -- ``/etc/yum.repos.d`` is root-owned --
    # so flag the request ``sudo=True`` (or ``su=True``); the transfer stays
    # unprivileged regardless.
    #
    # A URL ``src`` -- downloading a remote ``.repo`` file -- is
    # deliberately dropped: ``dnf.repo`` *constructs* the file from its
    # arguments, so a ``src`` with a URL scheme raises ``ValueError``. So does a
    # ``present`` request with no ``baseurl`` (there is nothing to construct the
    # file from), a ``gpgkey`` token that is neither a URL nor an absolute path,
    # and a ``proxy`` that is neither a URL nor dnf's ``_none_`` disable token.
    # To place a vendor's ``.repo`` straight from a URL, use ``files.download``.
    # ``**kwargs`` absorbs any further global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``).
    #
    # ``includepkgs``, ``repo_gpgcheck`` and the ``proxy*`` options are a
    # reemote extension (a baseline ``dnf.repo`` has none of them),
    # mirroring the ansible-datadog role's ``yum_repository`` fields. Each is
    # omitted from the file when None, so a request that sets none of them
    # constructs exactly the baseline content and stays a no-op against
    # a file an older release wrote. They append in a fixed order after
    # ``gpgkey`` so the content -- and thus the idempotence compare -- stays
    # deterministic. A ``proxy_password`` installs the file ``0600`` rather
    # than ``0644``; like ``files.install``, idempotence is content-only, so a
    # mode-only drift does not re-install.
    if urlparse(src).scheme:
        raise ValueError(
            "dnf.repo does not support a URL src (it constructs the file from "
            "its arguments; use files.download to place a URL .repo); pass a "
            f"repository name and baseurl instead, got: {src}"
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

    if proxy is not None and proxy != "_none_" and not urlparse(proxy).scheme:
        raise ValueError(f"proxy must be a URL or '_none_', got: {proxy}")

    lines = [
        f"[{src}]",
        f"name={description or src}",
        f"baseurl={baseurl}",
        f"enabled={1 if enabled else 0}",
        f"gpgcheck={1 if gpgcheck else 0}",
    ]
    if gpgkey:
        lines.append(f"gpgkey={gpgkey}")
    if repo_gpgcheck is not None:
        lines.append(f"repo_gpgcheck={1 if repo_gpgcheck else 0}")
    if includepkgs:
        lines.append(f"includepkgs={includepkgs}")
    if proxy is not None:
        lines.append(f"proxy={proxy}")
    if proxy_username is not None:
        lines.append(f"proxy_username={proxy_username}")
    if proxy_password is not None:
        lines.append(f"proxy_password={proxy_password}")
    lines.append("")  # trailing newline, matching the constructed file
    content = "\n".join(lines)
    mode = "0600" if proxy_password is not None else "0644"

    if current == content:
        ctx.reason = f"repo file already up to date ({path})"
    else:
        ctx.changed = True
        ctx.reason = f"repo file {path} missing or differs from the desired content"
        spool = _repo_spool_path(path)
        yield DnfRepoUploadCommand(content=content, dest=spool)
        yield DnfRepoInstallCommand(spool=spool, dest=path, mode=mode)


# --- dnf.makecache / dnf.clean ------------------------------------------------

#: The cache kinds ``dnf clean`` accepts (dnf's own subcommand arguments).
_CLEAN_KINDS = ("all", "metadata", "packages", "dbcache", "expire-cache")


@dataclass(frozen=True)
class DnfMakecacheCommand(Command):
    """``dnf -y makecache``: download/refresh repository metadata, optionally
    scoped to ``repos`` with ``--disablerepo='*' --enablerepo=<id>`` flags
    (the ansible-datadog role's repo-scoped cache refresh) and/or bare
    ``--disablerepo=<id>`` flags from ``disable_repos`` (the role's
    ``disablerepo: appstream`` shape, also reached from
    ``dnf.packages(update_cache=True, disable_repos=...)``). Never upgrades a
    package -- unlike ``DnfUpdateCommand`` -- and never touches another
    repo's cache when scoped."""

    repos: tuple[str, ...] = ()
    disable_repos: tuple[str, ...] = ()

    def shell(self) -> str:
        return f"dnf -y makecache{_repo_scope_args(self.repos, self.disable_repos)}"


async def makecache(
    ctx: OpContext,
    repos: str | list[str] | tuple[str, ...] | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Download/refresh dnf repository metadata, optionally scoped to repos.

    Args:
        ctx (OpContext): Execution context for the operation.
        repos (str | list[str] | tuple[str, ...] | None, optional): Repository
            ids to scope the refresh to (``--disablerepo='*'`` plus one
            ``--enablerepo`` per id). Prefer tuples when building requests via
            ``op()``, since ``OperationRequest`` kwargs must stay hashable.
            Defaults to None (every enabled repo).
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The makecache command, always (the operation is stateless).

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/dnf/dnf_makecache.py``.
    """

    # A reemote original (there is no other makecache operation), mirroring the
    # ansible-datadog role's `dnf -y makecache --disablerepo="*"
    # --enablerepo=datadog` step: after a repo file changes, its metadata must
    # be fetched before an install can resolve packages from it (dnf's own
    # expiry would otherwise serve a stale or absent cache).
    #
    # **Non-idempotent** like ``dnf.update``: it reads no fact, always reports
    # a change and always runs -- dnf itself is cheap on a warm cache. The
    # role gates the refresh on the repo task's `changed` flag; reemote
    # cannot gate on a same-deploy write without tripping the determinism
    # guard (the fact flips between prepare and apply), so the refresh is
    # unconditional and belongs right after the ``dnf.repo`` request in the
    # same deploy. The role also marks the step ``failed_when: false``;
    # reemote keeps its fail-fast convention instead -- a failing makecache
    # raises ``CommandError`` at apply. Writing the shared metadata cache
    # needs root, so flag the request ``sudo=True`` (or ``su=True``).
    scoped = _normalize_repos(repos)
    ctx.changed = True
    ctx.reason = f"dnf makecache ({', '.join(scoped)})" if scoped else "dnf makecache"
    yield DnfMakecacheCommand(repos=scoped)


async def clean(
    ctx: OpContext,
    what: str = "all",
    repos: str | list[str] | tuple[str, ...] | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Remove dnf cached data, optionally scoped to repos.

    Args:
        ctx (OpContext): Execution context for the operation.
        what (str, optional): The cache kind to remove: ``all``, ``metadata``,
            ``packages``, ``dbcache`` or ``expire-cache``. Defaults to "all".
        repos (str | list[str] | tuple[str, ...] | None, optional): Repository
            ids to scope the removal to (``--disablerepo='*'`` plus one
            ``--enablerepo`` per id). Prefer tuples when building requests via
            ``op()``, since ``OperationRequest`` kwargs must stay hashable.
            Defaults to None (every repo).
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The clean command, always (the operation is stateless).

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/dnf/dnf_clean.py``.
    """

    # A reemote original (there is no other clean operation), mirroring the
    # ansible-datadog role's `dnf clean metadata --disablerepo="*"
    # --enablerepo=datadog` step: a repo whose *content* moved (e.g. an agent
    # major-version downgrade) keeps serving stale cached metadata until it
    # is explicitly dropped -- expiry alone will not fix a downgrade.
    # ``dnf.packages(clean=True)`` cannot express this: it is an unscoped
    # ``dnf clean all`` over every repo's metadata *and* packages.
    #
    # **Non-idempotent** like ``dnf.update``/``dnf.makecache``: no fact is
    # read, the command always runs (and is itself cheap when the cache is
    # already empty). Unlike the role's ``failed_when: false`` the command is
    # fail-fast -- reemote's convention. Removing cached data needs root, so
    # flag the request ``sudo=True`` (or ``su=True``).
    if what not in _CLEAN_KINDS:
        raise ValueError(
            f"what must be one of {', '.join(_CLEAN_KINDS)}, got: {what}"
        )
    scoped = _normalize_repos(repos)
    ctx.changed = True
    ctx.reason = (
        f"dnf clean {what} ({', '.join(scoped)})" if scoped else f"dnf clean {what}"
    )
    yield DnfCleanCommand(what=what, repos=scoped)


# --- dnf.rpm -----------------------------------------------------------------


@dataclass(frozen=True)
class RpmInstallCommand(Command):
    """``rpm -i <src>``: install a ``.rpm`` file already on the target host.

    ``src`` is shell-quoted. Unlike a defensive ``ensure_rpm`` -- which, for a
    file it could not read at plan time (a URL downloaded mid-deploy), emitted
    a defensive ``rpm -q \\`rpm -qp <src>\\` ... || rpm -i <src>`` runtime check
    -- reemote always installs with a plain ``rpm -i``: it re-materializes the
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

    # ``dnf.rpm``: install or remove a ``.rpm`` file package.
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
    # A URL ``src`` -- downloading a remote ``.rpm`` mid-deploy -- is
    # deliberately dropped: ``dnf.rpm`` installs a *binary* package file that
    # must already be on the host, and reemote's URL download (``files.download``)
    # is text-only, so a ``src`` with a URL scheme raises ``ValueError``
    # (mirroring ``dnf.repo``). Stage the ``.rpm`` control-side and ``files.put``
    # it onto the host first. Because removal reads the package name from the file, a
    # ``present=False`` request whose ``.rpm`` is absent from the host cannot
    # resolve a name and is a no-op -- the same limitation documented for
    # an un-downloaded URL source. ``**kwargs`` absorbs any further global
    # arguments (``op()`` already strips ``name``/``sudo``/``su``).
    if urlparse(src).scheme:
        raise ValueError(
            "dnf.rpm does not support a URL src (it installs a binary .rpm "
            "that must already be on the host; files.download is text-only); "
            f"put the .rpm on the host first, got: {src}"
        )

    info = await ctx.facts.get(RpmPackage, src)
    # ``installed_name`` is set only when the file's package is installed at the
    # file's exact version, so it doubles as the ``exists`` flag and
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


def _key_spool_path() -> str:
    """The deterministic ``/tmp`` spool *file* path ``dnf.key`` uploads to.

    ``dnf.key`` imports the key into the rpm keyring rather than writing a
    managed file, so there is no destination path to derive a name from; the
    directory is keyed on a fixed constant instead, which keeps the value
    identical across the prepare and apply phases (a per-materialization temp
    name would diverge and trip the determinism guard). The stream is spooled
    to a fixed ``key.gpg`` file inside that directory rather than at the top
    level of ``/tmp``, and the elevated import ``rm -rf``s the whole directory
    afterwards, so no leftover lands in ``/tmp``.
    """
    digest = hashlib.sha1(b"dnf.key").hexdigest()[:16]
    return f"/tmp/reemote-dnf-key-{digest}/key.gpg"


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
        # scp does not create parent directories, and the spool now lives
        # inside a sha1-derived /tmp directory rather than at the top level,
        # so mkdir -p it first. Unprivileged: /tmp is world-writable, so the
        # login user can create the directory, and a transfer never elevates.
        parent = shlex.quote(str(PurePosixPath(self.dest).parent))
        await host.run_shell(f"mkdir -p {parent}")
        await host.upload_file(self.src, self.dest)


@dataclass(frozen=True)
class DnfKeyImportCommand(Command):
    """``rpm --import`` the spooled key, then remove the spool file.

    The second half of ``dnf.key`` and the elevated one: importing into the
    system rpm keyring needs root, so the request is flagged ``sudo=True`` and
    the import-and-remove is wrapped in ``sh -c``. A bare ``sudo rpm --import
    <path> && rm -rf <dir>`` would elevate only the import and leave the ``rm``
    running as the login user, whereas ``sudo sh -c '...'`` runs both steps as
    root, which can always remove the login-user spool directory. The spool
    file lives inside a sha1-derived ``/tmp`` directory, so the cleanup removes
    the whole directory rather than the bare file. Both paths are shell-quoted.
    """

    path: str

    def shell(self) -> str:
        quoted = shlex.quote(self.path)
        spool_dir = shlex.quote(str(PurePosixPath(self.path).parent))
        inner = f"rpm --import {quoted} && rm -rf {spool_dir}"
        return f"sh -c {shlex.quote(inner)}"


@dataclass(frozen=True)
class DnfKeyRemoveCommand(Command):
    """``rpm -e gpg-pubkey-<keyid>-<release>``: erase one imported key.

    The ``present=False`` half of ``dnf.key``. An imported key lives in the
    rpm database as a ``gpg-pubkey`` pseudo-package whose VERSION is the
    lowercased short key id and whose RELEASE is the hex creation timestamp,
    so removing it is a package erase, not keyring-file surgery. Erasing
    needs root, so the request is flagged ``sudo=True`` (or ``su=True``);
    this is a *single* command, so the driver's elevation prefix covers the
    whole shell and no ``sh -c`` wrapper is needed (unlike the import's
    ``&& rm`` chain). The pseudo-package name is shell-quoted as one
    argument.
    """

    keyid: str
    release: str

    def shell(self) -> str:
        return f"rpm -e {shlex.quote(f'gpg-pubkey-{self.keyid}-{self.release}')}"


async def key(
    ctx: OpContext,
    src: IO[Any] | None = None,
    keyid: str | None = None,
    present: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Import a GPG key into, or remove one from, the target's rpm keyring.

    Args:
        ctx (OpContext): Execution context for the operation.
        src (IO[Any] | None, optional): The key content as a seekable IO-like
            object on the control machine (an ``io.BytesIO``/``io.StringIO``,
            a file opened with ``open()``, ...), rewound to position 0 before
            it is read. Required when ``present=True``; rejected when
            ``present=False``. Defaults to None.
        keyid (str | None, optional): The key to remove when ``present=False``:
            8 hex digits (rpm's short key id) or 16 (the long form, matched by
            its last 8 digits), case-insensitive. Required when
            ``present=False``, ignored when ``present=True``. Defaults to None.
        present (bool, optional): ``True`` imports ``src`` into the rpm
            keyring; ``False`` removes the key ``keyid`` names from it.
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The key transfer and import commands, always, when
            ``present=True`` (that direction is stateless); the removal
            command only when ``present=False`` and the key is in the rpm
            keyring.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/dnf/dnf_key.py``.
    """

    # ``dnf.key`` -- import direction.
    #
    # The ``present=True`` direction is
    # **non-idempotent**: it always imports, so it always reports a change and
    # yields its commands on every run (rpm itself deduplicates an
    # already-imported key). It yields *two* descriptors from the one request
    # -- a ``DnfKeyUploadCommand`` transfer that spools the stream to a
    # ``/tmp`` path over scp, then a ``DnfKeyImportCommand`` running the
    # elevated ``rpm --import`` on that spool file and removing it. Because
    # the import needs root, flag the request ``sudo=True`` (or ``su=True``);
    # the transfer stays unprivileged regardless (transfers ride the
    # connection as the login user), so the two commands of one request run at
    # different privilege levels -- the driver dispatches the transfer to scp
    # and elevates only the shell import.
    #
    # A baseline ``dnf.key`` takes a ``src`` that is a *filename or URL* the
    # target host reads directly (``rpm --import <src>``). reemote does not
    # support controller-side file locations, so ``src`` is an IO-like
    # *stream* of the key content instead, and the bytes reach the host over
    # the connection's scp channel rather than by naming a path rpm can open;
    # a ``str`` ``src`` raises ``ValueError``. ``**kwargs`` absorbs any
    # global arguments (``op()`` already strips ``name``/``sudo``/``su``).
    #
    # Removal direction -- a reemote extension (import alone would not
    # cover removal), mirroring Ansible's ``rpm_key
    # state=absent``: an imported key lives in the rpm database as a
    # ``gpg-pubkey-<keyid>-<release>`` pseudo-package, so removal is an
    # elevated ``rpm -e`` of that package. It IS idempotent -- the
    # same-namespace ``RpmGpgKeys`` fact reports which keys are imported, and
    # the erase is yielded only for a key actually present (the ``gpg.key``
    # precedent: an in-namespace fact lifts the apt.key-style
    # non-idempotence). The ``gpg`` namespace's removal operations are *not*
    # an alternative: they manage GPG keyring files, never the rpm database.
    if present:
        if src is None:
            raise ValueError(
                "dnf.key requires src (an IO stream of the key content) when "
                "present=True"
            )
        if isinstance(src, str):
            # ValueError (not TypeError) keeps dnf.key consistent with
            # dnf.repo and dnf.rpm, which also reject an unusable string
            # ``src`` with ValueError.
            raise ValueError(
                "dnf.key does not support a filename/URL src (reemote takes "
                "IO streams, not controller-side file locations); pass the "
                f"key content as an IO-like object, got: {src!r}"
            )
        ctx.changed = True
        ctx.reason = f"import GPG key from {getattr(src, 'name', '<stream>')}"
        spool = _key_spool_path()
        yield DnfKeyUploadCommand(src=src, dest=spool)
        yield DnfKeyImportCommand(path=spool)
        return

    if keyid is None or not keyid.strip():
        raise ValueError("dnf.key requires keyid when present=False")
    if src is not None:
        raise ValueError(
            "dnf.key does not use src when present=False; name the key to "
            "remove with keyid instead"
        )
    # rpm stores the key id lowercased as the pseudo-package's VERSION; a
    # 16-digit long form matches by its last 8 digits (the short form rpm
    # records). Anything else simply matches no entry and plans a no-op.
    wanted = keyid.strip().lower()
    if len(wanted) == 16:
        wanted = wanted[-8:]
    keys = await ctx.facts.get(RpmGpgKeys)
    matches = [entry for entry in keys if entry["keyid"] == wanted]
    if matches:
        ctx.changed = True
        ctx.reason = f"GPG key {wanted} present in the rpm keyring, will remove"
        for entry in matches:
            yield DnfKeyRemoveCommand(keyid=entry["keyid"], release=entry["release"])
    else:
        ctx.reason = f"GPG key {wanted} not in the rpm keyring"


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

    # ``dnf.module``: enable or disable a module stream.
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
    # ``stream`` is required when ``enabled=True`` and a missing
    # one raises ``ValueError`` -- a house ``OperationValueError`` is
    # itself a ``ValueError`` subclass, so reemote uses the plain ``ValueError``
    # its sibling operations (``dnf.repo``/``dnf.rpm``/``dnf.key``) already raise.
    # The stream-switching caveat is inherited unchanged: ``dnf module
    # enable`` refuses to switch streams while packages from the active stream
    # are installed, so those must be removed (or the module ``dnf module
    # reset``) first -- this operation does not do that for you. ``**kwargs``
    # absorbs any further global arguments (``op()`` already strips
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
    "DnfKeyRemoveCommand",
    "DnfKeyUploadCommand",
    "DnfMakecacheCommand",
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
    "RpmGpgKey",
    "RpmGpgKeys",
    "RpmInstallCommand",
    "RpmPackage",
    "RpmPackageInfo",
    "clean",
    "key",
    "makecache",
    "module",
    "packages",
    "repo",
    "rpm",
    "update",
]
