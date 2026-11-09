# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# The ``snap`` module: facts and operations in one source.
#
# Fact side: ``SnapPackage`` is a port of pyinfra's ``snap.SnapPackage`` --
# one installed snap package's identity (name, publisher, snap-id, tracked
# channel and installed version) parsed from the labelled field rows of
# ``snap info <package>`` -- and ``SnapPackages`` ports its sibling
# ``snap.SnapPackages``, the bare ``snap list`` names of every installed snap.
# Like every fact they are phase-unaware: their ``process`` coroutine just
# gathers the raw value.
#
# Operation side: ``snap.package`` mirrors pyinfra's ``snap.package`` (install,
# remove, or switch the tracked channel of snap packages) through its
# ``SnapInstallCommand``/``SnapRemoveCommand``/``SnapRefreshCommand``
# descriptors. Operations are async generators that receive an ``OpContext``,
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

# --- Facts -------------------------------------------------------------------


class SnapPackageInfo(TypedDict):
    """
    Attributes:
        name: The snap's name (the ``name:`` field), e.g. ``"lxd"``.
        publisher: The publisher as snap prints it, verification glyph
            included (``"Canonical✓"``, ``"Canonical**"``); ``""`` when the
            package reports none.
        snap_id: The snap's store id (the ``snap-id:`` field); ``""`` when
            snap prints none.
        channel: The tracked channel read from the ``tracking:`` row as
            ``<track>/<risk>`` (``"4.0/stable"``, ``"latest/stable"``); the
            branch (a trailing ``/ubuntu-20.04``) is dropped. ``""`` when
            snap prints none.
        version: The installed version read from the ``installed:`` row
            (``"4.0.9-8e2046b"``); ``""`` when snap prints none.
    """

    # One snap package's identity (``SnapPackage``'s entry). The bare
    # singular ``SnapPackage`` slot is taken by the fact class, so the entry
    # carries the ``Info`` suffix (the ``ApkPackageInfo`` / ``DebPackageInfo``
    # / ``RpmPackageInfo`` convention).
    #
    # A TypedDict rather than a class instance: the answer is an ordinary
    # dict, so equality against a literal dict works and ``json.dumps``
    # serializes it directly. The ``snap_id`` field renames pyinfra's
    # hyphenated ``snap-id`` key onto a valid identifier (the same
    # label-to-key normalization ``ApkPackageInfo`` makes of apk's
    # ``webpage``/``installed size`` headers).

    name: str
    publisher: str
    snap_id: str
    channel: str
    version: str


class SnapPackage:
    """Detailed information about one installed snap package, as a ``SnapPackageInfo``.

    **Commands:**

    ```bash
    snap info <package> 2>/dev/null || true
    ```

    **Returns:**

    ```python
    SnapPackageInfo | None
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/snap/snap_snap_package.py``.
    """

    # Mirrors pyinfra's ``snap.SnapPackage``: ``snap info <package>`` prints a
    # block of labelled rows; the five tracked ones become one
    # ``SnapPackageInfo`` (for example ``{"name": "lxd", "publisher":
    # "Canonical✓", "snap_id": "J60k4JY0HppjwOjW8dZdYc8obXKxujRu", "channel":
    # "4.0/stable", "version": "4.0.9-8e2046b"}``). The regexes are pyinfra's
    # verbatim, applied to the raw line (``re.match``), so only the top-level,
    # unindented field rows are read: the indented ``channels:`` table, the
    # multi-line ``description: |`` body, the ``commands:``/``services:`` lists
    # and the ``refresh-date:``/``store-url:`` rows never match. The
    # ``channel`` is projected from ``tracking:`` down to ``<track>/<risk>``
    # (``4.0/stable/ubuntu-20.04`` -> ``"4.0/stable"``) and the ``version``
    # from ``installed:`` to the leading version token
    # (``4.0.9-8e2046b            (22753)  71MB -`` -> ``"4.0.9-8e2046b"``),
    # exactly as pyinfra's capture groups do.
    #
    # Two deliberate deviations from pyinfra. pyinfra's ``requires_command``
    # gating on ``snap`` is dropped and the command is wrapped in
    # ``2>/dev/null || true``: an absent package -- or a host without ``snap``
    # -- prints nothing to stdout (the ``error: no snap found ...`` line goes
    # to stderr, suppressed) and exits non-zero, so the guard reads as empty
    # output rather than raising ``CommandNotFoundError``. pyinfra's fact then
    # returns its ``default = dict`` (an empty ``{}``); reemote5 reports
    # ``None`` instead, the ``ApkPackage``/``DebPackage``/``RpmPackage``
    # None-on-absent convention, so a caller tests ``info is not None``. A
    # present package whose optional field is simply absent reads that field
    # as its empty-string default, keeping the entry's five keys always
    # present. The package name is ``shlex``-quoted, so a caller-supplied name
    # cannot break out of the argument.

    #: The five tracked ``snap info`` rows, each result key mapped to the
    #: pyinfra regex that reads its value from the raw line (``snap_id``
    #: reads the ``snap-id:`` row, ``channel`` the ``tracking:`` row and
    #: ``version`` the ``installed:`` row).
    FIELD_REGEXES: ClassVar[dict[str, re.Pattern[str]]] = {
        "name": re.compile(r"^name:[ ]+(.*)"),
        "publisher": re.compile(r"^publisher:[ ]+(.*)"),
        "snap_id": re.compile(r"^snap-id:[ ]+(.*)"),
        "channel": re.compile(r"^tracking:[ ]+([\w\d.-]+/[\w\d.-]+)[/]?.*$"),
        "version": re.compile(r"^installed:[ ]+([\w\d.-]+).*$"),
    }

    @classmethod
    def command(cls, package: str) -> str:
        """Shell reporting ``package``'s identity over SSH.

        ``snap info <package>`` prints the package's labelled field rows. The
        ``package`` argument is ``shlex``-quoted, and the ``2>/dev/null ||
        true`` tail swallows the non-zero exit an absent package (or a host
        without ``snap``) produces, so nothing reaches stdout and ``process``
        reports ``None`` -- the ``DebPackage`` tolerance rather than pyinfra's
        ``requires_command`` gating and empty-dict answer.
        """
        return f"snap info {shlex.quote(package)} 2>/dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> SnapPackageInfo | None:
        """Parse ``snap info`` field rows into a ``SnapPackageInfo``.

        Each raw line is matched against every tracked regex; a match writes
        its capture group into the corresponding field (the ``channel`` and
        ``version`` groups are the narrowed ``<track>/<risk>`` and version
        tokens, the others the whole rest of the line). Fields never matched
        keep their empty-string default. Returns ``None`` when no tracked row
        matched at all -- an absent package printed nothing (the command's
        ``|| true`` guard). Pure and testable.
        """
        data: SnapPackageInfo = {
            "name": "",
            "publisher": "",
            "snap_id": "",
            "channel": "",
            "version": "",
        }
        found = False
        for line in output:
            for key, regex in cls.FIELD_REGEXES.items():
                if match := regex.match(line):
                    data[key] = match.group(1)  # type: ignore[literal-required]
                    found = True
        if not found:
            return None
        return data

    @classmethod
    async def process(cls, host: Host, package: str) -> SnapPackageInfo | None:
        snap_package_info = getattr(host, "snap_package_info", None)
        if snap_package_info is not None:
            # SimulatedHost seam: the package-keyed store holds the parsed
            # answers; a name absent from it reads as None, like an absent
            # package. Copy the dict so callers never mutate simulated state.
            info = snap_package_info.get(package)
            return dict(info) if info is not None else None
        # Real transport: query the package's fields over asyncssh.
        stdout = await host.run_shell(cls.command(package))
        return cls.parse(stdout.splitlines())


class SnapPackages:
    """The names of the installed snap packages.

    **Commands:**

    ```bash
    snap list 2>/dev/null || true
    ```

    **Returns:**

    ```python
    list[str]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/snap/snap_snap_packages.py``.
    """

    # Mirrors pyinfra's ``snap.SnapPackages``: ``snap list`` prints a table
    # whose first line is the ``Name Version Rev Tracking Publisher Notes``
    # header, so the header is discarded and each remaining row contributes
    # its leading whitespace-delimited token -- the ``Name`` column. pyinfra
    # returns a bare list of names and reemote5 keeps that ``list[str]``
    # scalar-list shape (the same exception ``Ipv4Addrs`` and the sibling
    # ``PermissiveDomains`` make: the answer is a list of scalars, not record
    # dicts, so there is no field to wrap in an entry TypedDict). Names come
    # back in ``snap list``'s own listing order, deduplicated at their first
    # occurrence (snap never repeats a name, so the dedup is a defensive
    # no-op), and a blank body line -- which pyinfra's ``split()[0]`` would
    # raise on -- is skipped rather than crash ``parse``.
    #
    # One deliberate deviation, shared with the sibling ``SnapPackage``.
    # pyinfra's ``requires_command`` gating on ``snap`` is dropped and the
    # command is wrapped in ``2>/dev/null || true``: a host without ``snap``
    # exits non-zero, so the guard reads as empty output rather than raising
    # ``CommandNotFoundError``. pyinfra's fact then returns its ``default =
    # list`` (an empty ``[]``) for a host with nothing installed; reemote5
    # keeps the empty list for both the nothing-installed and the
    # no-``snap``-binary cases, so a caller tests ``if packages:`` and never
    # sees the tool's absence as an error.
    #
    # Seam note: ``process`` reads the *same* ``snap_package_info`` store the
    # sibling ``SnapPackage`` and the ``snap.package`` mutators edit, projecting
    # its keys -- the installed names -- rather than owning a parallel list the
    # mutators would have to keep in sync (the ``apt.AptPackages``/``apt_packages``
    # projection). The name set is therefore never inconsistent between the two
    # facts on a simulated host.

    #: Shell listing the installed snaps over SSH. The ``2>/dev/null ||
    #: true`` tail swallows the non-zero exit a host without ``snap``
    #: produces, so an unavailable tool reads as ``[]`` (see the class
    #: comment), matching the sibling ``SnapPackage``'s guarded command.
    COMMAND = "snap list 2>/dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[str]:
        """Parse ``snap list`` rows into installed snap names.

        The first line is the table header and is discarded; every
        remaining non-blank row yields its leading token, the ``Name``
        column, in listing order with duplicates dropped. Empty output --
        nothing installed, or the guarded command on a host without
        ``snap`` -- reads as ``[]``. Pure and testable.
        """
        names: list[str] = []
        for raw in list(output)[1:]:
            line = raw.strip()
            if not line:
                continue
            name = line.split()[0]
            if name not in names:
                names.append(name)
        return names

    @classmethod
    async def process(cls, host: Host) -> list[str]:
        snap_package_info = getattr(host, "snap_package_info", None)
        if snap_package_info is not None:
            # SimulatedHost seam: project the shared snap_package_info store's
            # keys -- the installed names the snap.package mutators edit -- so
            # the two snap facts can never disagree. The dict's key view is
            # copied into a fresh list so callers never mutate simulated state.
            return list(snap_package_info)
        # Real transport: list the installed snaps over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


# --- Operations --------------------------------------------------------------


@dataclass(frozen=True)
class SnapInstallCommand(Command):
    """``snap install [--classic] <packages> --channel=<channel>``: install snaps.

    The ``present=True`` direction of ``snap.package``, rendered in pyinfra's
    exact token order -- the confinement flag between the verb and the names,
    the channel option last. ``classic`` adds snap's ``--classic`` flag
    (confine-free access to system resources, the shape most non-store snaps
    need); it belongs to ``install`` only, so ``SnapRefreshCommand`` carries no
    such field. The package names and the channel are shell-quoted, so a
    metacharacter can never reach the remote shell unescaped -- a normal
    channel like ``latest/stable`` quotes to itself and renders verbatim.
    """

    packages: tuple[str, ...]
    channel: str = "latest/stable"
    classic: bool = False

    def shell(self) -> str:
        command = "snap install"
        if self.classic:
            command += " --classic"
        names = " ".join(shlex.quote(package) for package in self.packages)
        return f"{command} {names} --channel={shlex.quote(self.channel)}"


@dataclass(frozen=True)
class SnapRefreshCommand(Command):
    """``snap refresh <packages> --channel=<channel>``: switch the tracked channel.

    The channel-change path of ``snap.package`` (pyinfra's ``snap refresh``
    branch): yielded for an already-installed snap whose tracked ``channel``
    differs from the requested one. snap has no separate "switch channel"
    verb -- a ``refresh`` carrying ``--channel`` is what moves an installed
    snap between channels -- so the descriptor names the tool's own verb. The
    names and the channel are shell-quoted as on ``SnapInstallCommand``.
    """

    packages: tuple[str, ...]
    channel: str = "latest/stable"

    def shell(self) -> str:
        names = " ".join(shlex.quote(package) for package in self.packages)
        return f"snap refresh {names} --channel={shlex.quote(self.channel)}"


@dataclass(frozen=True)
class SnapRemoveCommand(Command):
    """``snap remove <packages>``: remove snaps.

    The ``present=False`` direction of ``snap.package`` (pyinfra's bare
    ``snap remove`` command). snap takes no channel or confinement flag when
    removing, so this descriptor carries the names alone, shell-quoted.
    """

    packages: tuple[str, ...]

    def shell(self) -> str:
        names = " ".join(shlex.quote(package) for package in self.packages)
        return f"snap remove {names}"


async def package(
    ctx: OpContext,
    packages: str | list[str] | tuple[str, ...] | None = None,
    channel: str = "latest/stable",
    classic: bool = False,
    present: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Install, remove or re-channel snap packages.

    Args:
        ctx (OpContext): Execution context for the operation.
        packages (str | list[str] | tuple[str, ...] | None, optional): Snaps
            to ensure. Prefer tuples when building requests via ``op()``, since
            ``OperationRequest`` kwargs must stay hashable. Defaults to None.
        channel (str, optional): The tracking channel to install on or switch
            an installed snap to. Defaults to ``"latest/stable"``.
        classic (bool, optional): Whether to install with ``--classic``
            confinement (access to system resources like traditional packages).
            Applies to the install direction only. Defaults to False.
        present (bool, optional): Whether the snaps should be installed.
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated snap package commands.

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/snap/snap_package.py``.
    """

    # pyinfra ``snap.package`` parity.
    #
    # Idempotence comes from the ``SnapPackage`` fact, awaited once per
    # requested snap: ``None`` means "not installed", and an installed entry
    # carries the tracked ``channel`` to compare against. That per-package
    # read deliberately replaces pyinfra's two-fact design -- upstream first
    # asks ``snap.SnapPackages`` (the bare ``snap list`` names) whether a snap
    # is installed and only then reads ``snap.SnapPackage`` for its channel,
    # but ``SnapPackage``'s None-on-absent answer already carries the same
    # presence information for the handful of names a request names, so one
    # fact serves both questions here (the ``SnapPackages`` fact exists for
    # callers wanting the whole fleet listing in one cheap call). A present
    # snap whose channel differs yields the channel switch pyinfra calls a
    # refresh (snap has no
    # separate ``switch`` verb); an absent one under ``present=False`` is the
    # no-op pyinfra reports as ``snap package X is not installed``, folded
    # into the shared ``"no packages to remove"`` reason the apt/dnf/apk
    # package operations use.
    #
    # Commands run in pyinfra's yield order: install, then remove, then
    # refresh. Installing, removing and refreshing all write system state, so
    # flag the request ``sudo=True`` (Ubuntu -- the snap fleet -- carries
    # passwordless ``sudo`` by default, unlike the Alpine/apk hosts). The
    # fact read itself is unprivileged: ``snap info`` is world-readable.
    # ``**kwargs`` absorbs pyinfra's global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``/``doas``).
    reasons: list[str] = []

    # Normalize: pyinfra accepts a bare string, a list, or None.
    if packages is None:
        requested: tuple[str, ...] = ()
    elif isinstance(packages, str):
        requested = (packages,)
    else:
        requested = tuple(packages)

    if not requested:
        ctx.reason = "no packages requested"
        return

    to_install: list[str] = []
    to_remove: list[str] = []
    to_refresh: list[str] = []
    for package_name in requested:
        info = await ctx.facts.get(SnapPackage, package_name)
        if present:
            if info is None:
                to_install.append(package_name)
            elif info["channel"] != channel:
                to_refresh.append(package_name)
        elif info is not None:
            to_remove.append(package_name)

    if present:
        if to_install:
            ctx.changed = True
            reasons.append(f"{len(to_install)} package(s) to install")
            yield SnapInstallCommand(
                packages=tuple(to_install), channel=channel, classic=classic
            )
        if to_refresh:
            ctx.changed = True
            reasons.append(f"{len(to_refresh)} package(s) to refresh")
            yield SnapRefreshCommand(packages=tuple(to_refresh), channel=channel)
        if not to_install and not to_refresh:
            reasons.append("all packages present")
    else:
        if to_remove:
            ctx.changed = True
            reasons.append(f"{len(to_remove)} package(s) to remove")
            yield SnapRemoveCommand(packages=tuple(to_remove))
        else:
            reasons.append("no packages to remove")

    ctx.reason = "; ".join(reasons)


__all__ = [
    "SnapInstallCommand",
    "SnapPackage",
    "SnapPackageInfo",
    "SnapPackages",
    "SnapRefreshCommand",
    "SnapRemoveCommand",
    "package",
]
