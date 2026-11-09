# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# The ``flatpak`` module: facts and operations in one source.
#
# Fact side: ``FlatpakPackage`` is a port of pyinfra's ``FlatpakPackage`` --
# one installed flatpak package's identity (application id, ref and version)
# parsed from the indented label rows of ``flatpak info <package>`` -- and
# ``FlatpakPackages`` ports its sibling ``flatpak.FlatpakPackages``, the bare
# ``flatpak list --columns=application`` IDs of every installed flatpak. Like
# every fact they are phase-unaware: their ``process`` coroutine just gathers
# the raw value.
#
# Operation side: ``flatpak.packages`` mirrors pyinfra's ``flatpak.packages``
# (install or remove flatpak applications, optionally from a named remote)
# through its ``FlatpakInstallCommand``/``FlatpakRemoveCommand`` descriptors.
# Operations are async generators that receive an ``OpContext``, await facts,
# set ``ctx.changed``/``ctx.reason``, and yield command descriptors; they never
# touch the host directly.


import re
import shlex
from collections.abc import AsyncGenerator, Iterable
from dataclasses import dataclass
from typing import Any, ClassVar, TypedDict

from ...commands import Command
from ...core import OpContext
from ...hosts import Host

# --- Facts -------------------------------------------------------------------


class FlatpakPackageInfo(TypedDict):
    """
    Attributes:
        id: The application's reverse-domain ID (the ``ID:`` row), e.g.
            ``"org.videolan.VLC"``.
        ref: The full install ref (the ``Ref:`` row),
            ``"app/org.videolan.VLC/x86_64/stable"``.
        version: The installed version read from the ``Version:`` row
            (``"3.0.21"``, ``"21.0-Omega"``); ``""`` when flatpak prints none
            (a runtime has no ``Version:`` row).
    """

    # One flatpak package's identity (``FlatpakPackage``'s entry). The bare
    # singular ``FlatpakPackage`` slot is taken by the fact class, so the entry
    # carries the ``Info`` suffix (the ``SnapPackageInfo`` / ``ApkPackageInfo`` /
    # ``DebPackageInfo`` convention).
    #
    # A TypedDict rather than a class instance: the answer is an ordinary
    # dict, so equality against a literal dict works and ``json.dumps``
    # serializes it directly. The three fields are pyinfra's verbatim
    # ``id``/``ref``/``version`` keys, already valid identifiers.

    id: str
    ref: str
    version: str


class FlatpakPackage:
    """Detailed information about one installed flatpak package, as a ``FlatpakPackageInfo``.

    **Commands:**

    ```bash
    flatpak info <package> 2>/dev/null || true
    ```

    **Returns:**

    ```python
    FlatpakPackageInfo | None
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/flatpak/flatpak_flatpak_package.py``.
    """

    # Mirrors pyinfra's ``flatpak.FlatpakPackage``: ``flatpak info <package>``
    # prints a block of label rows; the three tracked ones (``ID:``, ``Ref:``
    # and ``Version:``) become one ``FlatpakPackageInfo`` (for example
    # ``{"id": "org.videolan.VLC", "ref": "app/org.videolan.VLC/x86_64/stable",
    # "version": "3.0.21"}``). The regexes are pyinfra's verbatim, applied to
    # the raw line (``re.match``), and each anchors on the row's leading
    # indentation, so only the indented label rows are read: the unindented
    # title line, the blank separators and every other label (``Arch``,
    # ``Branch``, ``License``, ``Origin``, ``Collection``, ``Installation``,
    # ``Installed``, ``Runtime``, ``Sdk`` and the commit block) never match. The
    # ``version`` group is pyinfra's narrowed ``[\\w\\d.-]+`` token, so the
    # whole non-numeric suffix of ``21.0-Omega`` is kept.
    #
    # Two deliberate deviations from pyinfra, shared with the sibling
    # ``snap.SnapPackage``. pyinfra's ``requires_command`` gating on ``flatpak``
    # is dropped and the command is wrapped in ``2>/dev/null || true``: an
    # absent package -- or a host without ``flatpak`` -- prints nothing to
    # stdout (the ``error: ... not installed`` line goes to stderr, suppressed)
    # and exits non-zero, so the guard reads as empty output rather than raising
    # ``CommandNotFoundError``. pyinfra's fact then returns its ``default =
    # dict`` (an empty ``{}``); reemote5 reports ``None`` instead, the
    # ``ApkPackage``/``DebPackage``/``RpmPackage`` None-on-absent convention, so
    # a caller tests ``info is not None``. A present package whose optional
    # ``Version:`` row is simply absent (a runtime) reads that field as its
    # empty-string default, keeping the entry's three keys always present. The
    # package name is ``shlex``-quoted, so a caller-supplied name cannot break
    # out of the argument.

    #: The three tracked ``flatpak info`` rows, each result key mapped to the
    #: pyinfra regex that reads its value from the raw indented line.
    FIELD_REGEXES: ClassVar[dict[str, re.Pattern[str]]] = {
        "id": re.compile(r"^[ ]+ID:[ ]+(.*)$"),
        "ref": re.compile(r"^[ ]+Ref:[ ]+(.*)$"),
        "version": re.compile(r"^[ ]+Version:[ ]+([\w\d.-]+).*$"),
    }

    @classmethod
    def command(cls, package: str) -> str:
        """Shell reporting ``package``'s identity over SSH.

        ``flatpak info <package>`` prints the package's labelled rows. The
        ``package`` argument is ``shlex``-quoted, and the ``2>/dev/null ||
        true`` tail swallows the non-zero exit an absent package (or a host
        without ``flatpak``) produces, so nothing reaches stdout and ``process``
        reports ``None`` -- the ``DebPackage`` tolerance rather than pyinfra's
        ``requires_command`` gating and empty-dict answer.
        """
        return f"flatpak info {shlex.quote(package)} 2>/dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> FlatpakPackageInfo | None:
        """Parse ``flatpak info`` rows into a ``FlatpakPackageInfo``.

        Each raw line is matched against every tracked regex; a match writes
        its capture group into the corresponding field (the ``version`` group
        is the narrowed version token, the others the whole rest of the line).
        Fields never matched keep their empty-string default. Returns ``None``
        when no tracked row matched at all -- an absent package printed nothing
        (the command's ``|| true`` guard). Pure and testable.
        """
        data: FlatpakPackageInfo = {"id": "", "ref": "", "version": ""}
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
    async def process(cls, host: Host, package: str) -> FlatpakPackageInfo | None:
        flatpak_package_info = getattr(host, "flatpak_package_info", None)
        if flatpak_package_info is not None:
            # SimulatedHost seam: the package-keyed store holds the parsed
            # answers; a name absent from it reads as None, like an absent
            # package. Copy the dict so callers never mutate simulated state.
            info = flatpak_package_info.get(package)
            return dict(info) if info is not None else None
        # Real transport: query the package's identity over asyncssh.
        stdout = await host.run_shell(cls.command(package))
        return cls.parse(stdout.splitlines())


class FlatpakPackages:
    """The application IDs of the installed flatpak packages.

    **Commands:**

    ```bash
    flatpak list --columns=application 2>/dev/null || true
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
        Examples can be found in ``examples/modules/flatpak/flatpak_packages.py``.
    """

    # Mirrors pyinfra's ``flatpak.FlatpakPackages``: ``flatpak list
    # --columns=application`` prints a single-column table whose first line is
    # the ``Application ID`` header, so the header is discarded and every
    # remaining row is one application ID (the dotted reverse-domain name, e.g.
    # ``org.videolan.VLC``). pyinfra returns a bare list of IDs and reemote5
    # keeps that ``list[str]`` scalar-list shape (the same exception
    # ``hardware.Ipv4Addrs`` and the sibling ``snap.SnapPackages`` make: the
    # answer is a list of scalars, not record dicts, so there is no field to
    # wrap in an entry TypedDict). IDs come back in flatpak's own listing
    # order, deduplicated at their first occurrence, and a blank body line is
    # skipped rather than crash ``parse``.
    #
    # One deliberate deviation, shared with ``snap.SnapPackages``. pyinfra's
    # ``requires_command`` gating on ``flatpak`` is dropped and the command is
    # wrapped in ``2>/dev/null || true``: a host without ``flatpak`` exits
    # non-zero, so the guard reads as empty output rather than raising
    # ``CommandNotFoundError``. pyinfra's fact then returns its ``default =
    # list`` (an empty ``[]``) for a host with nothing installed; reemote5 keeps
    # the empty list for both the nothing-installed and the no-``flatpak``-
    # binary cases, so a caller tests ``if packages:`` and never sees the tool's
    # absence as an error. pyinfra's header filter (``if i > 1 or flatpak !=
    # "Application ID"``) is an off-by-one that keeps a second header-looking
    # row; reemote5 drops any line equal to the ``Application ID`` header,
    # which is what the upstream regex was reaching for.

    #: Shell listing the installed flatpak application IDs over SSH. The
    #: ``2>/dev/null || true`` tail swallows the non-zero exit a host without
    #: ``flatpak`` produces, so an unavailable tool reads as ``[]`` (see the
    #: class comment).
    COMMAND = "flatpak list --columns=application 2>/dev/null || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[str]:
        """Parse ``flatpak list --columns=application`` rows into IDs.

        The ``Application ID`` header and any blank line are discarded; every
        remaining non-blank row contributes its stripped value -- one dotted
        application ID -- in listing order with duplicates dropped. Empty
        output -- nothing installed, or the guarded command on a host without
        ``flatpak`` -- reads as ``[]``. Pure and testable.
        """
        apps: list[str] = []
        for raw in output:
            line = raw.strip()
            if not line or line == "Application ID":
                continue
            if line not in apps:
                apps.append(line)
        return apps

    @classmethod
    async def process(cls, host: Host) -> list[str]:
        flatpak_package_info = getattr(host, "flatpak_package_info", None)
        if flatpak_package_info is not None:
            # SimulatedHost seam: project the *same* package-keyed store
            # FlatpakPackage reads, so the install/remove mutators keep both
            # facts consistent by construction (the snap.SnapPackages
            # precedent). Copy so callers never mutate simulated state.
            return list(flatpak_package_info)
        # Real transport: list the installed flatpaks over asyncssh.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


# --- Operations --------------------------------------------------------------


@dataclass(frozen=True)
class FlatpakInstallCommand(Command):
    """``flatpak install --noninteractive [remote] <packages>``: install flatpaks.

    The ``present=True`` direction of ``flatpak.packages``, rendered in
    pyinfra's exact token order -- the ``--noninteractive`` flag right after
    the verb, then the optional remote, then the application IDs. The remote is
    the source flatpak resolves each ID against; when it is ``None`` the token
    is omitted entirely and flatpak falls back to its configured remotes (the
    same ``if remote: append`` branch pyinfra makes after stripping the value).
    The remote and every application ID are shell-quoted, so a metacharacter can
    never reach the remote shell unescaped -- a normal ID like
    ``org.videolan.VLC`` quotes to itself and renders verbatim.
    """

    packages: tuple[str, ...]
    remote: str | None = None

    def shell(self) -> str:
        parts = ["flatpak install --noninteractive"]
        if self.remote:
            parts.append(shlex.quote(self.remote))
        parts.extend(shlex.quote(package) for package in self.packages)
        return " ".join(parts)


@dataclass(frozen=True)
class FlatpakRemoveCommand(Command):
    """``flatpak uninstall --noninteractive <packages>``: remove flatpaks.

    The ``present=False`` direction of ``flatpak.packages`` (pyinfra's bare
    ``flatpak uninstall --noninteractive`` command). flatpak takes no remote
    when removing -- the application is already resolved -- so this descriptor
    carries the IDs alone, shell-quoted.
    """

    packages: tuple[str, ...]

    def shell(self) -> str:
        names = " ".join(shlex.quote(package) for package in self.packages)
        return f"flatpak uninstall --noninteractive {names}"


async def packages(
    ctx: OpContext,
    packages: str | list[str] | tuple[str, ...] | None = None,
    remote: str | None = None,
    present: bool = True,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Install or remove flatpak application packages.

    Args:
        ctx (OpContext): Execution context for the operation.
        packages (str | list[str] | tuple[str, ...] | None, optional):
            Application IDs to ensure. Prefer tuples when building requests via
            ``op()``, since ``OperationRequest`` kwargs must stay hashable.
            Defaults to None.
        remote (str | None, optional): The remote to install the applications
            from (e.g. ``"flathub"``); omitted means flatpak's default. Ignored
            when removing. Defaults to None.
        present (bool, optional): Whether the applications should be installed.
            Defaults to True.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated flatpak package commands.

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/flatpak/flatpak_packages.py``.
    """

    # pyinfra ``flatpak.packages`` parity.
    #
    # Idempotence comes from the ``FlatpakPackages`` fact, awaited once: an
    # application ID absent from the installed list under ``present=True`` is
    # queued for install, and one present under ``present=False`` is queued for
    # removal. An absent ID under ``present=False`` is the no-op pyinfra reports
    # as ``flatpak package X is not installed``, folded into the shared
    # ``"no packages to remove"`` reason the apt/dnf/apk package operations use.
    # Presence is a bare name-only membership test (flatpak application IDs carry
    # no version to compare, unlike apk's pins) -- the same rule ``dnf.packages``
    # applies.
    #
    # Commands run in pyinfra's yield order: install, then remove. Installing
    # and removing a system flatpak write system state, so flag the request
    # ``sudo=True`` (Ubuntu carries passwordless ``sudo`` by default). The
    # ``remote`` is applied to the install direction only, as in pyinfra, and
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

    # pyinfra strips and treats an empty remote as absent (``if remote:``).
    if remote is not None:
        remote = remote.strip() or None

    installed = await ctx.facts.get(FlatpakPackages)

    to_install: list[str] = []
    to_remove: list[str] = []
    for package_name in requested:
        if present:
            if package_name not in installed:
                to_install.append(package_name)
        elif package_name in installed:
            to_remove.append(package_name)

    if present:
        if to_install:
            ctx.changed = True
            reasons.append(f"{len(to_install)} package(s) to install")
            yield FlatpakInstallCommand(
                packages=tuple(to_install), remote=remote
            )
        else:
            reasons.append("all packages present")
    else:
        if to_remove:
            ctx.changed = True
            reasons.append(f"{len(to_remove)} package(s) to remove")
            yield FlatpakRemoveCommand(packages=tuple(to_remove))
        else:
            reasons.append("no packages to remove")

    ctx.reason = "; ".join(reasons)


__all__ = [
    "FlatpakInstallCommand",
    "FlatpakPackage",
    "FlatpakPackageInfo",
    "FlatpakPackages",
    "FlatpakRemoveCommand",
    "packages",
]
