# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# The ``pip`` module: facts and operations in one source.
#
# Fact side: ``PipPackages`` reports the installed pip packages as
# ``PipPackageInfo`` entries (PEP 503-normalized name plus the version
# ``pip freeze`` prints), parsed from the ``<name>==<version>`` rows of
# ``<pip> freeze --all``, one entry per installed name. Like every fact it is
# phase-unaware: its ``process`` coroutine just gathers the raw value.
#
# Operation side: ``pip.packages`` installs, upgrades or removes pip
# packages -- bare names, ``<pkg>==<version>`` pins or a requirements file --
# through its ``PipInstallCommand``/``PipUpgradeCommand``/
# ``PipRemoveCommand``/``PipRequirementsCommand`` descriptors. Operations are
# async generators that receive an ``OpContext``, await facts, set
# ``ctx.changed``/``ctx.reason``, and yield command descriptors; they never
# touch the host directly.


import re
import shlex
from collections.abc import AsyncGenerator, Iterable
from dataclasses import dataclass
from typing import Any, ClassVar, TypedDict

from ...commands import Command
from ...core import OpContext
from ...hosts import Host

# --- Shared helpers ----------------------------------------------------------

#: The characters that begin a PEP 508 requirement name: letters, digits and
#: the ``-``, `_`, `.` and `+` punctuation a name may carry. The token run of
#: this class extracts the bare name out of any spec, pinned or not.
_NAME_TOKEN_REGEX = re.compile(r"^[A-Za-z0-9._+\-]+")


def _normalize_name(name: str) -> str:
    """The PEP 503-normalized form of a pip package name.

    PEP 503 (adopted by PEP 426's comparison rule pyinfra cites) declares
    ``-``, ``_`` and ``.`` equivalent separators and the name
    case-insensitive, so ``Zope.Interface``, ``zope-interface`` and
    ``zope.interface`` are one package. The normalization collapses runs of
    separators to a single ``-`` and lowercases; both ``PipPackages.parse``
    and ``pip.packages`` compare through this one helper, so a request's
    spelling and the ``pip freeze`` row always meet in the same form.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def _split_pin(spec: str) -> tuple[str, str | None]:
    """Split a requirement spec into ``(name, version)``; version is None unless pinned.

    An ``==`` pin splits at its *first* occurrence -- the ``<pkg>==<version>``
    spelling pip itself documents -- and both halves are returned verbatim so
    the caller can quote the pin through to the shell. Any other PEP 508
    specifier (``>=``, ``~=``, ``!=``, ``<``) is deliberately *not*
    interpreted: reemote carries no ``packaging`` dependency, so the leading
    name token is returned with version ``None`` and presence degrades to the
    name-only check -- the same treatment pyinfra gives an unsupported
    multi-specifier requirement, minus its warning. Pure and testable.
    """
    name, sep, version = spec.partition("==")
    if sep:
        return name, version
    match = _NAME_TOKEN_REGEX.match(spec)
    return (match.group(), None) if match else (spec, None)


# --- Facts -------------------------------------------------------------------


class PipPackageInfo(TypedDict):
    """
    Attributes:
        name: The PEP 503-normalized package name (``"zope-interface"`` for
            whatever spelling ``pip freeze`` printed).
        version: The installed version exactly as ``pip freeze`` printed it
            (``"2.31.0"``, ``"1.0.0.post1"``, ``"2024.2-1"``).
    """

    # One pip package at one version -- the entry of the ``PipPackages``
    # fact. A TypedDict rather than a class instance: the answer is an
    # ordinary name/version pair, so equality against a literal dict works
    # and ``json.dumps`` serializes it directly -- the
    # ``ApkPackageVersion``/``FlatpakPackageInfo`` convention of every
    # package-list fact in this codebase. The name is normalized at parse
    # time because comparison is normalized (see ``_normalize_name``); the
    # version stays verbatim, since pip has no canonical version spelling to
    # normalize to.

    name: str
    version: str


class PipPackages:
    """The installed pip packages, as ``PipPackageInfo`` entries.

    **Commands:**

    ```bash
    <pip> freeze --all
    ```

    **Returns:**

    ```python
    list[PipPackageInfo]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/pip/pip_packages.py``.
    """

    # The reemote shape of pyinfra's ``pip.PipPackages`` fact. The ``pip``
    # argument selects the environment -- a bare ``pip``/``pip3`` on the PATH
    # or the ``bin/pip`` of a virtualenv -- and defaults to ``"pip"``;
    # ``pip.packages`` threads its own ``pip`` option through so the fact
    # always reports the environment the operation mutates. pyinfra's
    # ``--all`` is kept: without it ``pip freeze`` hides pip, setuptools and
    # wheel, and an operation managing those names would never see them
    # installed.
    #
    # Two deliberate deviations from pyinfra. pyinfra returns a
    # ``dict[str, list[str]]``; reemote reports sorted, unique
    # ``PipPackageInfo`` entries -- the deterministic, JSON-printable
    # answer shape every package-list fact here uses (the
    # ``ApkInstalledPackages`` precedent). And pyinfra gates the fact on its
    # ``requires_command`` (no pip, no command, silently an empty dict);
    # reemote runs the command bare, so a missing pip exits 127 and surfaces
    # as ``CommandNotFoundError`` -- an environment without pip is an error
    # the deployer hears about, not a silently empty fact. The name is PEP
    # 503-normalized in ``parse`` because the comparison below is
    # normalized; simulated state must therefore key the seam store by the
    # normalized name too.

    #: One ``pip freeze`` row: the ``<name>==<version>`` token pyinfra's
    #: ``PIP_REGEX`` matches, name group over the PEP 503 name characters and
    #: version group over the digits/dots lead plus the lowercase
    #: alnum/dash suffix (``1.0.0.post1`` splits as digits ``1.0.0.``, suffix
    #: ``post1``; ``21.0-Omega`` fails the lowercase class and is skipped, as
    #: pyinfra skips it). Editable ``-e ...`` and ``@ url`` rows match nothing
    #: and are skipped.
    PACKAGE_REGEX: ClassVar[re.Pattern[str]] = re.compile(
        r"^([a-zA-Z0-9_\-\+\.]+)==([0-9\.]+[a-z0-9\-]*)$"
    )

    @classmethod
    def command(cls, pip: str = "pip") -> str:
        """Shell listing ``pip``'s environment over SSH.

        ``<pip> freeze --all`` prints one ``<name>==<version>`` row per
        installed distribution, system packages included. The ``pip``
        executable is ``shlex``-quoted, so a virtualenv path carrying a
        metacharacter cannot break out of the argument.
        """
        return f"{shlex.quote(pip)} freeze --all"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[PipPackageInfo]:
        """Parse ``pip freeze`` rows into sorted, unique entries.

        Only lines matching ``PACKAGE_REGEX`` contribute; each contributes one
        ``PipPackageInfo`` whose ``name`` is the row's name normalized via
        ``_normalize_name`` and whose ``version`` is the printed version
        verbatim. A repeated name collapses into the entry seen last, and the
        result is sorted by name. Empty output -- a bare environment, or one
        whose rows all fail the regex -- reads as ``[]``. Pure and testable.
        """
        by_name: dict[str, str] = {}
        for line in output:
            match = cls.PACKAGE_REGEX.match(line.strip())
            if match:
                by_name[_normalize_name(match.group(1))] = match.group(2)
        return [{"name": name, "version": by_name[name]} for name in sorted(by_name)]

    @classmethod
    async def process(cls, host: Host, pip: str = "pip") -> list[PipPackageInfo]:
        pip_packages = getattr(host, "pip_packages", None)
        if pip_packages is not None:
            # SimulatedHost seam: the store is one mapping of normalized
            # name to version -- one environment, whatever executable the
            # caller named -- keyed exactly as ``parse`` keys its answer, so
            # simulated state must use the normalized form. Copy each entry
            # so callers never mutate simulated state.
            return [
                {"name": name, "version": version}
                for name, version in sorted(pip_packages.items())
            ]
        # Real transport: freeze the environment over asyncssh.
        stdout = await host.run_shell(cls.command(pip))
        return cls.parse(stdout.splitlines())


# --- Operations --------------------------------------------------------------


@dataclass(frozen=True)
class PipInstallCommand(Command):
    """``<pip> install [extra_args] <packages>``: install packages.

    The ``present=True`` direction of ``pip.packages``. The pip executable and
    every package token -- bare names and ``<pkg>==<version>`` pins alike --
    are shell-quoted, so a metacharacter can never reach the remote shell
    unescaped. ``extra_args`` is pyinfra's ``extra_install_args``: free-form
    arguments for the install command, appended *unquoted* right after the
    verb -- the ``dnf`` ``extra_args`` convention, which makes ``--index-url
    <url>``-style fragments expressible at the cost of treating the value as a
    raw shell fragment the caller must keep safe.
    """

    pip: str
    packages: tuple[str, ...]
    extra_args: str | None = None

    def shell(self) -> str:
        parts = [shlex.quote(self.pip), "install"]
        if self.extra_args:
            parts.append(self.extra_args)
        parts.extend(shlex.quote(package) for package in self.packages)
        return " ".join(parts)


@dataclass(frozen=True)
class PipUpgradeCommand(Command):
    """``<pip> install [extra_args] --upgrade <packages>``: upgrade packages.

    The ``latest`` upgrade path of ``pip.packages`` -- pyinfra's
    ``upgrade_command``, built as the install command plus ``--upgrade`` so
    the extra install arguments apply here too. Token order is pip's own: the
    flag sits between the extra arguments and the package names. Names are
    shell-quoted and ``extra_args`` is appended unquoted exactly as on
    ``PipInstallCommand``.
    """

    pip: str
    packages: tuple[str, ...]
    extra_args: str | None = None

    def shell(self) -> str:
        parts = [shlex.quote(self.pip), "install"]
        if self.extra_args:
            parts.append(self.extra_args)
        parts.append("--upgrade")
        parts.extend(shlex.quote(package) for package in self.packages)
        return " ".join(parts)


@dataclass(frozen=True)
class PipRemoveCommand(Command):
    """``<pip> uninstall --yes <packages>``: remove packages.

    The ``present=False`` direction of ``pip.packages`` (pyinfra's
    ``uninstall_command``): the ``--yes`` flag answers pip's confirmation
    prompt so the removal is non-interactive. ``packages`` holds bare
    normalized-intent names, never ``==`` pins -- pip's *uninstall* rejects
    requirement specifiers -- and every name is shell-quoted.
    """

    pip: str
    packages: tuple[str, ...]

    def shell(self) -> str:
        names = " ".join(shlex.quote(package) for package in self.packages)
        return f"{shlex.quote(self.pip)} uninstall --yes {names}"


@dataclass(frozen=True)
class PipRequirementsCommand(Command):
    """``<pip> install [extra_args] [--upgrade] -r <path>`` / ``<pip> uninstall --yes -r <path>``.

    The ``requirements`` direction of ``pip.packages``, one descriptor for
    both directions (the ``ApkVirtualCommand`` precedent). ``present=True``
    installs the file -- with ``--upgrade`` when ``latest`` asks for it,
    pyinfra's ``upgrade_command ... -r`` form; ``present=False`` removes every
    package the file names. The pip executable and the file path are
    shell-quoted, the extra install arguments appended unquoted, and the
    ``-r`` token is pip's own requirements-file flag.
    """

    pip: str
    path: str
    present: bool = True
    latest: bool = False
    extra_args: str | None = None

    def shell(self) -> str:
        quoted_pip = shlex.quote(self.pip)
        if not self.present:
            return f"{quoted_pip} uninstall --yes -r {shlex.quote(self.path)}"
        parts = [quoted_pip, "install"]
        if self.extra_args:
            parts.append(self.extra_args)
        if self.latest:
            parts.append("--upgrade")
        parts.extend(["-r", shlex.quote(self.path)])
        return " ".join(parts)


async def packages(
    ctx: OpContext,
    packages: str | list[str] | tuple[str, ...] | None = None,
    present: bool = True,
    latest: bool = False,
    requirements: str | None = None,
    pip: str = "pip",
    extra_install_args: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Install, upgrade or remove pip packages.

    Args:
        ctx (OpContext): Execution context for the operation.
        packages (str | list[str] | tuple[str, ...] | None, optional): Packages
            to ensure. Prefer tuples when building requests via ``op()``, since
            ``OperationRequest`` kwargs must stay hashable. Versions can be
            pinned as ``<pkg>==<version>``. Defaults to None.
        present (bool, optional): Whether the packages should be installed.
            Defaults to True.
        latest (bool, optional): Whether to upgrade installed packages without
            a pinned version. Defaults to False.
        requirements (str | None, optional): Path of a requirements file to
            install (or remove when ``present=False``); the file is applied on
            every run, the operation is stateless for it. Defaults to None.
        pip (str, optional): The pip executable -- a bare ``pip``/``pip3`` on
            the PATH or a virtualenv's ``bin/pip`` path -- used for every
            command and for the ``PipPackages`` fact. Defaults to "pip".
        extra_install_args (str | None, optional): Free-form arguments appended
            (unquoted) to the install command. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated pip package commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/pip/pip_packages.py``.
    """

    # pyinfra ``pip.packages`` parity.
    #
    # The ``PipPackages`` fact supplies the installed versions, so a pinned
    # ``<pkg>==<version>`` is present only when that *exact* version is
    # installed (the ``ensure_packages`` version match, the same rule
    # ``apt.packages`` and ``apk.packages`` follow), and ``latest`` follows
    # pyinfra's legacy behavior: every installed, unpinned package is
    # upgraded with ``--upgrade`` without checking whether an upgrade
    # actually exists. Requests and fact entries meet at the PEP 503
    # normalized name, so ``Zope-Interface`` and ``zope.interface`` are one
    # package -- pyinfra lowercases only, which a ``_``/``.`` spelling would
    # miss.
    #
    # The ``requirements`` option is stateless: like pyinfra, a given file
    # always yields its command and never compares against installed state
    # (reading it would need host file facts the file itself may change), and
    # it is yielded *before* any package command, the order pyinfra emits.
    #
    # Deliberate deviations from pyinfra. Its ``virtualenv`` and
    # ``virtualenv_kwargs`` options are dropped: ``virtualenv_kwargs`` is a
    # dict, which breaks the hashable-request rule ``op()`` enforces, and its
    # auto-creation would delegate to ``pip.virtualenv`` -- an operation
    # reemote does not (yet) provide. A virtualenv is targeted instead by
    # passing its interpreter as ``pip=<venv>/bin/pip``. Other PEP 508
    # specifiers (``>=``, ``~=``) are not interpreted -- reemote carries no
    # ``packaging`` dependency -- so they fall back to name-only presence via
    # ``_split_pin``. And the removal direction feeds pip *bare names*, not
    # the ``<pkg>==<version>`` tokens pyinfra passes to ``pip uninstall``,
    # which pip rejects.
    #
    # Installing into a system interpreter's site-packages needs root, so
    # flag such requests ``sudo=True``; a per-user or virtualenv pip does not.
    # ``**kwargs`` absorbs pyinfra's global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``/``doas``).
    reasons: list[str] = []

    # Requirements first, always: the direction is stateless, so the command
    # is yielded on every run (pyinfra's ``if requirements is not None``).
    if requirements is not None:
        ctx.changed = True
        if present:
            reasons.append(f"requirements file {requirements} to install")
        else:
            reasons.append(f"requirements file {requirements} to remove")
        yield PipRequirementsCommand(
            pip=pip,
            path=requirements,
            present=present,
            latest=latest,
            extra_args=extra_install_args,
        )

    # Normalize: accept a bare string, a list, or None.
    if packages is None:
        requested: tuple[str, ...] = ()
    elif isinstance(packages, str):
        requested = (packages,)
    else:
        requested = tuple(packages)

    if requested:
        installed = await ctx.facts.get(PipPackages, pip)
        # The fact reports one entry per installed name; group the versions
        # by (normalized) name so the lookups below read as the version-list
        # comparison ``ensure_packages`` does.
        versions_by_name: dict[str, list[str]] = {}
        for entry in installed:
            versions_by_name.setdefault(entry["name"], []).append(entry["version"])

        def is_installed(spec: str) -> bool:
            name, version = _split_pin(spec)
            versions = versions_by_name.get(_normalize_name(name))
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
                    if _split_pin(pkg)[1] is None and is_installed(pkg)
                )
                if latest
                else ()
            )
            if to_install:
                ctx.changed = True
                reasons.append(f"{len(to_install)} package(s) to install")
                yield PipInstallCommand(
                    pip=pip, packages=to_install, extra_args=extra_install_args
                )
            if to_upgrade:
                ctx.changed = True
                reasons.append(f"{len(to_upgrade)} package(s) to upgrade")
                yield PipUpgradeCommand(
                    pip=pip, packages=to_upgrade, extra_args=extra_install_args
                )
            if not to_install and not to_upgrade:
                reasons.append("all packages present")
        else:
            # Version-aware like pyinfra (a pin is removed only when that
            # version is installed), but fed to pip as bare names, since
            # ``pip uninstall`` rejects ``==`` requirement tokens.
            to_remove = tuple(
                dict.fromkeys(
                    _split_pin(pkg)[0]
                    for pkg in requested
                    if is_installed(pkg)
                )
            )
            if to_remove:
                ctx.changed = True
                reasons.append(f"{len(to_remove)} package(s) to remove")
                yield PipRemoveCommand(pip=pip, packages=to_remove)
            else:
                reasons.append("no packages to remove")
    else:
        if not reasons:
            reasons.append("no packages requested")

    ctx.reason = "; ".join(reasons)


__all__ = [
    "PipInstallCommand",
    "PipPackageInfo",
    "PipPackages",
    "PipRemoveCommand",
    "PipRequirementsCommand",
    "PipUpgradeCommand",
    "packages",
]
