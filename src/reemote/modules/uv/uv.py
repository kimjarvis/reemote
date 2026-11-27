# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# The ``uv`` module: facts (and later operations) in one source.
#
# Fact side: ``Packages`` reports the Python packages of the environment
# ``uv pip list`` resolves as ``PackageInfo`` entries (name plus the version
# uv reports), parsed from the JSON array ``uv pip list --format json``
# prints. ``Info`` condenses the installation itself -- uv's version, the
# executable on the PATH and the resolved tool, tool-bin, python and cache
# directories -- into one ``InstallInfo`` record gathered in a single
# chained shell. ``Tools`` reads ``uv tool list`` into sorted ``ToolInfo``
# entries (name, version, exposed entrypoints), and ``Pythons`` lists the
# interpreters present on the host as ``PythonInfo`` entries, flagging each
# one uv-managed or system-discovered. Which environment ``Packages``
# reports -- an active or discovered ``.venv``, or the interpreter uv falls
# back to -- is uv's own resolution, exactly as pyinfra leaves it to the
# tool, unless the fact's optional ``venv`` argument names one (uv's
# ``--python``). Like every fact it is phase-unaware: its ``process``
# coroutine just gathers the raw value.
#
# Operation side: the six pyinfra ``uv`` operations -- ``packages`` (uv pip),
# ``pythons``, ``tools``, ``tools_upgrade_all``, ``tools_update_shell`` and
# ``venv`` -- are async generators that receive an ``OpContext``, await the
# facts above (and ``server.Env`` for the PATH), set ``ctx.changed`` /
# ``ctx.reason``, and yield one typed command descriptor per mutation; they
# never touch the host directly. The idempotent three (``packages``,
# ``pythons``, ``tools``) compare against ``Packages``/``Pythons``/``Tools``
# exactly as ``pip.packages`` compares against ``PipPackages``, and ``venv``
# reconciles through a private existence probe like ``pip.venv``. Operations
# and their descriptors live in this source below the facts.


import json
import posixpath
import re
import shlex
from collections.abc import AsyncGenerator, Iterable
from dataclasses import dataclass
from typing import Any, ClassVar, TypedDict

from ...commands import Command
from ...core import OpContext
from ...hosts import Host
from ..server.server import Env

# --- Facts -------------------------------------------------------------------


class PackageInfo(TypedDict):
    """
    Attributes:
        name: The package name exactly as uv spells it (``"pydantic"``,
            ``"typing-extensions"``, ``"zope.interface"``).
        version: The installed version as uv reports it (``"2.11.4"``,
            ``"1.1.0b4"``, ``"2024.2-1"``).
    """

    # One uv-listed package at one version -- the entry of the ``Packages``
    # fact. A TypedDict rather than a class instance: the answer is an
    # ordinary name/version pair, so equality against a literal dict works
    # and ``json.dumps`` serializes it directly -- the
    # ``PipPackageInfo``/``NpmPackageInfo`` convention of every package-list
    # fact in this codebase. Unlike pip there is no name to normalize: uv
    # prints the canonical name of each distribution and pyinfra's
    # ``UvPipPackages`` performs no normalization either, so the reported
    # name is the one uv printed.

    name: str
    version: str


class Packages:
    """The Python packages in uv's environment, as ``PackageInfo`` entries.

    **Commands:**

    ```bash
    uv pip list --format json
    uv pip list --format json --python <venv>
    ```

    **Returns:**

    ```python
    list[PackageInfo]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/uv/uv_packages.py``.
    """

    # The reemote shape of pyinfra's ``uv.UvPipPackages`` fact. The command
    # is pyinfra's verbatim: ``uv pip list --format json`` prints one JSON
    # array of ``{"name": ..., "version": ...}`` objects on stdout (uv's own
    # "Using Python ... environment at: ..." notice goes to stderr and never
    # reaches the parse), and which environment is listed -- ``VIRTUAL_ENV``,
    # a discovered ``.venv``, or a fallback interpreter -- is uv's resolution
    # rule, shared with pyinfra.
    #
    # The optional ``venv`` argument is a reemote addition with no pyinfra
    # counterpart (pyinfra's ``uv.packages`` has no environment selector at
    # all): uv's ``--python`` names the environment to read, and it accepts the
    # root directory of a virtual environment as readily as an interpreter path
    # or version request. ``uv.packages`` threads its own ``venv`` option here
    # so the listing always reports the environment the commands mutate -- the
    # ``pip.packages``/``PipPackages`` mechanism, spelled ``--python`` because
    # uv has no separate executable to name.
    #
    # Three deliberate deviations from pyinfra. pyinfra's ``process_json``
    # returns a ``dict[str, set[str]]`` keyed by the printed name and warns
    # on a JSON load failure; reemote reports sorted, unique ``PackageInfo``
    # entries -- the deterministic, JSON-printable answer shape every
    # package-list fact here uses (the ``ApkInstalledPackages`` precedent) --
    # and parses total: unparsable output, a non-list document, a
    # non-mapping row or a row without string ``name``/``version`` values
    # contribute nothing, the ``NpmPackages`` rule, where pyinfra's
    # key-first indexing would raise. And pyinfra gates the fact on its
    # ``requires_command = "uv"`` (no uv, no command, silently an empty
    # dict); reemote runs the command bare, so a missing uv exits 127 and
    # surfaces as ``CommandNotFoundError`` -- an environment without uv is
    # an error the deployer hears about, not a silently empty fact. Names are
    # kept verbatim (uv prints canonical names), so -- unlike the pip
    # fact -- simulated state keys the seam store by the printed name.

    #: Shell gathering the JSON listing over SSH; stdout is the bare JSON
    #: array, uv's environment notice riding on stderr.
    COMMAND: ClassVar[str] = "uv pip list --format json"

    @classmethod
    def command(cls, venv: str | None = None) -> str:
        """Shell listing one Python environment over SSH.

        ``uv pip list --format json`` with no ``venv`` -- byte-identical to
        :attr:`COMMAND`, leaving the environment to uv's own resolution -- or
        the same listing scoped by ``--python <venv>``, where ``venv`` is a
        virtual environment's root directory, an interpreter path or a version
        request. The value is ``shlex``-quoted, so a path carrying a
        metacharacter cannot break out of the argument.
        """
        if venv is None:
            return cls.COMMAND
        return f"{cls.COMMAND} --python {shlex.quote(venv)}"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[PackageInfo]:
        """Parse ``uv pip list --format json`` rows into sorted, unique entries.

        The JSON document is the text from the first ``[`` onwards, so any
        leading noise is discarded, and unparsable output -- or a document
        that is not a list -- reads as ``[]``. Each mapping row contributes a
        ``PackageInfo`` of its string ``name`` and ``version`` verbatim; any
        other row contributes nothing. A repeated name collapses into the
        entry seen last, and the result is sorted by name. Empty output -- a
        bare environment, or uv failing to resolve one (the error text is
        stderr, so stdout stays empty) -- reads as ``[]``, the counterpart of
        pyinfra's ``default = dict``. Pure and testable.
        """
        text = "\n".join(output)
        start = text.find("[")
        if start == -1:
            return []
        try:
            decoded = json.loads(text[start:])
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError, RecursionError):
            return []
        if not isinstance(decoded, list):
            return []
        by_name: dict[str, str] = {}
        for entry in decoded:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            version = entry.get("version")
            if isinstance(name, str) and isinstance(version, str):
                by_name[name] = version
        return [{"name": name, "version": by_name[name]} for name in sorted(by_name)]

    @classmethod
    async def process(cls, host: Host, venv: str | None = None) -> list[PackageInfo]:
        uv_packages = getattr(host, "uv_packages", None)
        if uv_packages is not None:
            # SimulatedHost seam: the store is one mapping of printed name to
            # version -- one environment, whatever ``venv`` the caller named --
            # keyed exactly as ``parse`` keys its answer, so simulated state
            # uses the verbatim uv spelling. Copy each entry so callers never
            # mutate simulated state.
            return [
                {"name": name, "version": version}
                for name, version in sorted(uv_packages.items())
            ]
        # Real transport: the JSON listing over asyncssh. A non-zero exit
        # that is not 127 (uv failing to resolve an environment, say) leaves
        # stdout empty, which parses as [].
        stdout = await host.run_shell(cls.command(venv))
        return cls.parse(stdout.splitlines())


class InstallInfo(TypedDict):
    """
    Attributes:
        version: The clean uv version number -- the leading ``uv `` token and
            any trailing build parenthetical dropped (``"0.9.22"`` from
            ``uv 0.9.22``, ``"0.12.21"`` from ``uv 0.12.21 (7af826859
            2026-09-29 x86_64-unknown-linux-gnu)``).
        executable: The absolute path ``command -v`` resolves ``uv`` to on
            the target shell's PATH (``"/snap/bin/uv"``).
        tool_dir: Where uv installs isolated tool environments, the
            ``uv tool dir`` answer (``"~/.local/share/uv/tools"``).
        tool_bin_dir: Where uv links the tools' executables, the
            ``uv tool dir --bin`` answer (``"~/.local/bin"``).
        python_dir: Where uv stores its managed Python installs, the
            ``uv python dir`` answer (``"~/.local/share/uv/python"``).
        cache_dir: uv's cache directory, the ``uv cache dir`` answer
            (``"~/.cache/uv"``).
    """

    # The one record ``Info`` returns -- six resolved strings, each ``None``
    # when the chained shell did not print its line (no uv, or an older uv
    # without one of the dir subcommands). A TypedDict rather than a class
    # instance: the answer is an ordinary mapping of strings, so equality
    # against a literal dict works and ``json.dumps`` serializes it
    # directly -- the entry-type convention of this module.

    version: str | None
    executable: str | None
    tool_dir: str | None
    tool_bin_dir: str | None
    python_dir: str | None
    cache_dir: str | None


class Info:
    """The uv installation: version, executable and resolved directories.

    **Commands:**

    ```bash
    uv --version && uv tool dir && uv tool dir --bin && uv python dir && uv cache dir && command -v uv
    ```

    **Returns:**

    ```python
    InstallInfo
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/uv/uv_info.py``.
    """

    # The design's consolidation of pyinfra's scattered uv scalars --
    # ``UvVersion``, ``UvPythonDir``, ``UvToolDir`` and the path lookups --
    # into one low-overhead fact: a single ``&&``-chained shell asks uv the
    # six questions in one round trip, and because each command prints
    # exactly one stdout line the parse maps the lines to the fields
    # positionally. Every directory comes from uv itself, so the answer
    # reflects the *effective* paths after the target shell's ``UV_*`` and
    # ``XDG_*`` overrides -- the same environment a later uv operation runs
    # in -- and ``command -v uv`` names the executable the chain just ran.
    #
    # Deliberate deviations from pyinfra, following the sibling
    # ``Packages``: the ``requires_command = "uv"`` gate is dropped, and the
    # chain anchors on ``uv --version``, so a host without uv exits 127 and
    # the gather raises ``CommandNotFoundError`` rather than skipping
    # silently; an older uv that lacks one dir subcommand (verified present
    # in uv 0.9) simply short-circuits the ``&&`` chain, and the fields past
    # that point read ``None`` -- pyinfra's per-fact ``default = ""``
    # answered the same absence as an empty string. The version is normalized
    # the way the design asks -- a clean version string: uv's leading ``uv ``
    # token and any trailing build parenthetical are dropped, so the release
    # binary's ``uv 0.12.21 (7af826859 2026-09-29 x86_64-unknown-linux-gnu)``
    # reads ``"0.12.21"``, where pyinfra's ``UvVersion`` kept the whole line.
    # ``uv self version`` exists on self-managed installs but prints the same
    # text, so the bare ``--version`` is the one probe; pyinfra's
    # ``2>/dev/null`` guards are dropped because ``run_shell`` reads stdout only.

    #: Shell gathering the whole record over SSH in one chained call; each
    #: command contributes exactly one stdout line, in ``FIELDS`` order.
    COMMAND: ClassVar[str] = (
        "uv --version && uv tool dir && uv tool dir --bin"
        " && uv python dir && uv cache dir && command -v uv"
    )

    # The positional line -> field mapping of the chained shell; the
    # ``&&``-chain order IS the field order, and the leading version line is
    # the one whose absence means uv is not installed at all (a 127 the
    # unchecked fact read still raises as CommandNotFoundError).
    FIELDS: ClassVar[tuple[str, ...]] = (
        "version",
        "tool_dir",
        "tool_bin_dir",
        "python_dir",
        "cache_dir",
        "executable",
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> InstallInfo:
        """Map the chained shell's lines onto the install fields, positionally.

        Blank lines are skipped (a successful uv command prints none, but a
        wrapper shell might), the first remaining line is the version --
        reduced to its leading token, so uv's ``uv `` prefix and any trailing
        build parenthetical are dropped -- and each later line fills the next
        ``FIELDS`` entry. A chain that short-circuited (or printed no lines at
        all) leaves the missing fields ``None``. Pure and testable.
        """
        lines = [line.strip() for line in output if line.strip()]
        values = dict(zip(cls.FIELDS, lines, strict=False))
        version = values.get("version")
        if version is not None:
            # Release and packager builds append a parenthetical to the number
            # (``uv 0.12.21 (7af826859 2026-09-29 x86_64-unknown-linux-gnu)``,
            # ``uv 0.8.5 (Homebrew 2025-08-05)``); the design wants a clean
            # version string, so only the leading token after the ``uv ``
            # prefix survives, and a bare prefix reads ``None``.
            tokens = version.removeprefix("uv ").split()
            version = tokens[0] if tokens else None
        return {
            "version": version,
            "tool_dir": values.get("tool_dir"),
            "tool_bin_dir": values.get("tool_bin_dir"),
            "python_dir": values.get("python_dir"),
            "cache_dir": values.get("cache_dir"),
            "executable": values.get("executable"),
        }

    @classmethod
    async def process(cls, host: Host) -> InstallInfo:
        uv_install = getattr(host, "uv_install", None)
        if uv_install is not None:
            # SimulatedHost seam: one mapping of field name to resolved
            # string. Every field is named in the literal and read through
            # ``get``, so the record stays total -- an unseeded field answers
            # ``None`` exactly like a chain line uv never printed -- and no
            # caller can mutate the simulated mapping through the result.
            return {
                "version": uv_install.get("version"),
                "executable": uv_install.get("executable"),
                "tool_dir": uv_install.get("tool_dir"),
                "tool_bin_dir": uv_install.get("tool_bin_dir"),
                "python_dir": uv_install.get("python_dir"),
                "cache_dir": uv_install.get("cache_dir"),
            }
        # Real transport: the one chained shell over asyncssh. A non-zero
        # exit that is not 127 (an old uv without a dir subcommand, say)
        # leaves the chain short, which parses as a partly-None record.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class ToolInfo(TypedDict):
    """
    Attributes:
        name: The tool package name exactly as uv spells it (``"ruff"``,
            ``"black"``).
        version: The installed version with uv's leading ``v`` stripped
            (``"0.8.4"``); ``None`` when the header names no version -- a tool
            installed from a local path or a git URL.
        entrypoints: The executables the tool placed on the PATH, sorted
            (``["black", "blackd"]``); empty when uv printed no bullets.
    """

    # One uv-installed tool -- the entry of the ``Tools`` fact. A TypedDict
    # rather than a class instance: the answer is an ordinary record of
    # strings, so equality against a literal dict works and ``json.dumps``
    # serializes it directly -- the ``PackageInfo`` convention of this module.

    name: str
    version: str | None
    entrypoints: list[str]


class Tools:
    """The isolated CLI tools uv has installed, as ``ToolInfo`` entries.

    **Commands:**

    ```bash
    uv tool list
    ```

    **Returns:**

    ```python
    list[ToolInfo]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/uv/uv_tools.py``.
    """

    # Mirrors pyinfra's ``uv.UvTools``: uv prints one two-token header per tool
    # (``ruff 0.8.4``) followed by a bullet for each executable it links
    # (``- ruff``). ``uv tool list`` has no JSON output (astral-sh/uv #10219,
    # pyinfra's own TODO), so the plain-text grammar is parsed.
    #
    # Three deliberate deviations from pyinfra. pyinfra skips every bullet and
    # warns a versionless header away, so ``["pyinfra"]`` answers ``{}`` and
    # nothing reports which binaries a tool placed on the PATH; reemote keeps
    # both halves -- a bare name becomes a ``ToolInfo`` with ``version=None``
    # and the bullets accumulate into ``entrypoints`` -- because the idempotence
    # checks asked for the entrypoints too. uv's empty-state hint (``No tools
    # installed``) is three tokens, which pyinfra files as a tool named ``No``
    # at version ``tools``; reemote reads any row that is neither a header nor a
    # bullet as noise and skips it. And the ``requires_command`` gate plus the
    # ``2>/dev/null`` guard are dropped, as in ``Packages``: a missing uv exits
    # 127 and raises ``CommandNotFoundError``. No tools installed -- uv
    # answering with the hint line alone -- reads as ``[]``, the counterpart of
    # pyinfra's ``default = dict``.

    #: Shell gathering the tool listing over SSH; one header line per tool,
    #: each followed by a bullet per entrypoint.
    COMMAND: ClassVar[str] = "uv tool list"

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[ToolInfo]:
        """Parse ``uv tool list`` rows into sorted, unique entries.

        A row of at most two tokens is a tool header: the second token, with
        uv's leading ``v`` stripped, is its version, and a header carrying no
        second token reports ``None`` instead of being dropped. A ``-`` bullet
        names an entrypoint of the tool most recently headed, and bullets
        before any header are noise. Any other row -- uv's hints, wrapper
        chatter -- is skipped. A repeated name restarts that tool's record, so
        the last header wins, and the result is sorted by name with each tool's
        entrypoints deduped and sorted. Pure and testable.
        """
        tools: dict[str, tuple[str | None, set[str]]] = {}
        current: str | None = None
        for raw_line in output:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("- "):
                if current is not None and (entrypoint := line[2:].strip()):
                    tools[current][1].add(entrypoint)
                continue
            pieces = line.split()
            if len(pieces) > 2:
                continue
            current = pieces[0]
            version = pieces[1].removeprefix("v") if len(pieces) == 2 else None
            tools[current] = (version, set())
        return [
            {"name": name, "version": version, "entrypoints": sorted(entrypoints)}
            for name, (version, entrypoints) in sorted(tools.items())
        ]

    @classmethod
    async def process(cls, host: Host) -> list[ToolInfo]:
        uv_tools = getattr(host, "uv_tools", None)
        if uv_tools is not None:
            # SimulatedHost seam: one mapping of tool name to its record, keyed
            # exactly as ``parse`` names its entries. A missing version reads
            # ``None`` and missing bullets an empty list, so a partially seeded
            # record stays well-typed, and each entrypoint list is copied so
            # callers never mutate simulated state.
            return [
                {
                    "name": name,
                    "version": record.get("version"),
                    "entrypoints": sorted(record.get("entrypoints", ())),
                }
                for name, record in sorted(uv_tools.items())
            ]
        # Real transport: the bare listing over asyncssh. A non-zero exit that
        # is not 127 leaves stdout empty, which parses as [].
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class PythonInfo(TypedDict):
    """
    Attributes:
        key: uv's own interpreter key (``"cpython-3.12.8-linux-x86_64-gnu"``);
            repeats when one interpreter is discovered at two paths.
        implementation: The implementation named by the key (``"cpython"``,
            ``"pypy"``).
        version: The interpreter version uv reports (``"3.12.8"``).
        path: The executable path uv resolved, anchored on the shell's working
            directory and normalized to absolute (``"/usr/bin/python3.12"``,
            ``"/root/.local/share/uv/python/.../bin/python3.14"``); the unique
            half of an entry's identity.
        managed: ``True`` when the resolved path lives under ``uv python dir``
            -- a uv-downloaded install -- and ``False`` for a system or
            otherwise discovered interpreter.
    """

    # One interpreter present on the host -- the entry of the ``Pythons``
    # fact. A TypedDict rather than a class instance: the answer is an ordinary
    # record of strings and a flag, so equality against a literal dict works
    # and ``json.dumps`` serializes it directly -- the ``PackageInfo``
    # convention of this module.

    key: str
    implementation: str
    version: str
    path: str
    managed: bool


class Pythons:
    """The Python interpreters present on the host, as ``PythonInfo`` entries.

    **Commands:**

    ```bash
    uv python dir && pwd && uv python list --only-installed --output-format=json
    ```

    **Returns:**

    ```python
    list[PythonInfo]
    ```

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/uv/uv_pythons.py``.
    """

    # The reemote shape of pyinfra's ``uv.UvInstalledPythonsByImplementation``
    # and ``uv.UvInstalledPythonsByVersion`` family, answered by one fact: uv's
    # JSON rows already carry the ``key``, ``implementation``, ``version`` and
    # ``path`` those two single-field views split apart.
    #
    # Deliberate deviations from pyinfra. The leading ``uv python dir && pwd``
    # is not pyinfra's -- uv's JSON has no managed flag, so the fact asks uv
    # where it keeps its own downloads and calls an interpreter managed when its
    # path lies under that directory. uv prints a managed download's path
    # relative to the shell's working directory whenever that directory is an
    # ancestor (the SSH chain runs from the login home, so
    # ``.local/share/uv/python/...`` instead of the absolute form
    # ``uv python dir`` itself prints), which is why the chain also captures
    # ``pwd`` and the parse anchors every relative path on it -- without that
    # anchoring a uv-managed interpreter reads as a system one. pyinfra instead
    # defaults to ``--managed-python``, which (verified against uv 0.9 and
    # 0.12) hides every system interpreter, so its answer cannot carry the
    # managed-versus-system split at all. The flag is dropped here while
    # ``--only-installed`` is kept, so a bare host reports what is present
    # rather than the whole download catalogue. And where
    # pyinfra's ``process_json`` builds a ``dict[str, set[str]]`` keyed by
    # ``key``, uv reuses one key for the same interpreter at several paths
    # (``cpython-3.13.5-linux-x86_64-gnu`` at both ``/usr/bin/python3.13`` and
    # ``/usr/bin/python3``), so a keyed dict silently overwrites; reemote returns
    # entries unique on ``(key, path)``. The parse stays total -- unparsable
    # JSON, a non-list document or a row whose four strings are absent
    # contribute nothing, where pyinfra warns or raises -- and the
    # ``requires_command`` gate is dropped, so a missing uv exits 127 and raises
    # ``CommandNotFoundError``. No interpreters, or uv failing to list, reads
    # as ``[]``.

    #: Shell gathering the managed Python directory, the working directory the
    #: JSON paths are relative to, and the installed interpreters, in one
    #: chained call: the first stdout line is ``uv python dir``, the second
    #: ``pwd``, the rest the JSON array.
    COMMAND: ClassVar[str] = (
        "uv python dir && pwd"
        " && uv python list --only-installed --output-format=json"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[PythonInfo]:
        """Split the chained shell and parse its interpreter rows.

        The first non-blank line is the ``uv python dir`` answer, the second
        the ``pwd`` the JSON paths hang off, and the text from the first ``[``
        onwards the JSON array -- leading noise is discarded, and unparsable
        output or a document that is not a list reads as ``[]``. Each mapping
        row holding string ``key``, ``implementation``, ``version`` and ``path``
        becomes a ``PythonInfo``: a relative path -- how uv prints a managed
        download found under the working directory -- is anchored on that
        ``pwd`` and normalized to absolute, and ``managed`` then asks whether
        the resolved path sits under the printed directory; any other row
        contributes nothing. Entries are unique on ``(key, path)`` -- uv repeats
        a key across paths -- and sorted by that pair. Pure and testable.
        """
        lines = [line.strip() for line in output if line.strip()]
        if len(lines) < 2:
            return []
        base = posixpath.normpath(lines[1])
        raw_dir = lines[0]
        python_dir = posixpath.normpath(
            raw_dir if posixpath.isabs(raw_dir) else posixpath.join(base, raw_dir)
        )
        text = "\n".join(lines[2:])
        start = text.find("[")
        if start == -1:
            return []
        try:
            decoded = json.loads(text[start:])
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError, RecursionError):
            return []
        if not isinstance(decoded, list):
            return []
        by_location: dict[tuple[str, str], PythonInfo] = {}
        for entry in decoded:
            if not isinstance(entry, dict):
                continue
            key = entry.get("key")
            implementation = entry.get("implementation")
            version = entry.get("version")
            path = entry.get("path")
            values = (key, implementation, version, path)
            if not all(isinstance(value, str) for value in values):
                continue
            # uv anchors a managed download's path on the shell's working
            # directory when that directory is an ancestor of it, so a relative
            # path is resolved against ``pwd`` before the comparison; system
            # interpreters arrive absolute and are only normalized.
            resolved = posixpath.normpath(
                path if posixpath.isabs(path) else posixpath.join(base, path)
            )
            by_location[(key, resolved)] = {
                "key": key,
                "implementation": implementation,
                "version": version,
                "path": resolved,
                "managed": resolved == python_dir
                or resolved.startswith(f"{python_dir}/"),
            }
        return [by_location[location] for location in sorted(by_location)]

    @classmethod
    async def process(cls, host: Host) -> list[PythonInfo]:
        uv_pythons = getattr(host, "uv_pythons", None)
        if uv_pythons is not None:
            # SimulatedHost seam: one mapping of interpreter path to its
            # record -- the path is the unique half of an entry's identity, so
            # one key found at two paths seeds two entries. Absent fields read
            # the empty defaults and ``managed`` is coerced, so a partially
            # seeded record stays well-typed.
            return [
                {
                    "key": record.get("key", ""),
                    "implementation": record.get("implementation", ""),
                    "version": record.get("version", ""),
                    "path": path,
                    "managed": bool(record.get("managed", False)),
                }
                for path, record in sorted(uv_pythons.items())
            ]
        # Real transport: the one chained shell over asyncssh. A non-zero exit
        # that is not 127 (uv unable to list, say) leaves the array absent,
        # which parses as [].
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


# --- Operations --------------------------------------------------------------

#: The characters that begin a PEP 508 requirement name: letters, digits and
#: the ``-``, ``_``, ``.`` and ``+`` a name may carry. Used to pull the bare
#: name out of an unpinned spec that carries no ``==`` separator.
_NAME_TOKEN_REGEX = re.compile(r"^[A-Za-z0-9._+\-]+")


def _split_pin(spec: str) -> tuple[str, str | None]:
    """Split a uv requirement spec into ``(name, version)``; version is None
    unless pinned with ``==``.

    An ``==`` pin splits at its first occurrence -- the ``<pkg>==<version>``
    spelling uv and pip both document -- and both halves return verbatim so the
    caller can quote the pin through to the shell. Any other PEP 508 specifier
    (``>=``, ``~=``, ``!=``, ``<``) is not interpreted: reemote carries no
    ``packaging`` dependency, so the leading name token is returned with
    version ``None`` and presence degrades to the name-only check -- the same
    treatment ``pip.packages`` gives an unsupported multi-specifier
    requirement.
    """
    name, sep, version = spec.partition("==")
    if sep:
        return name, version
    match = _NAME_TOKEN_REGEX.match(spec)
    return (match.group(), None) if match else (spec, None)


def _as_tuple(value: str | list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    """Normalize an operation's package/version/tool argument to a tuple.

    pyinfra accepts a bare string or a list; ``op()`` requires hashable
    kwargs, so a sequence arrives as a tuple. ``None`` reads as the empty
    tuple (nothing requested).
    """
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def _version_requested_matches(requested: str, installed: str) -> bool:
    """Whether an installed interpreter version satisfies a request.

    uv resolves a request like ``"3.13"`` to a full ``"3.13.4"``, so a request
    matches an installed version when the request's dotted components are a
    prefix of the installed one: ``"3.13"`` matches ``"3.13.4"`` and
    ``"3.13"``, ``"3"`` matches every ``3.*``, and ``"3.13.4"`` matches only
    itself. A request that is *longer* than the installed version (an exact
    micro the host does not carry) never matches. Pure, so ``pythons`` and the
    simulated ``install_uv_python`` mutator share one rule.
    """
    parts = requested.split(".")
    return installed.split(".")[: len(parts)] == parts


@dataclass(frozen=True)
class UvPipInstallCommand(Command):
    """``uv pip install --no-progress [--python <venv>] [extra_args] <packages>``: install packages.

    The ``present=True`` direction of ``uv.packages`` (pyinfra's
    ``install_command``). The ``uv`` verb is a literal; every package token --
    bare name or ``<pkg>==<version>`` pin -- is shell-quoted, ``venv`` is the
    optional environment named with ``--python`` (shell-quoted), and
    ``extra_args`` is pyinfra's free-form argument appended *unquoted* after it
    (the ``dnf``/``pip`` convention, which makes an ``--index-url <url>``
    fragment expressible at the cost of treating the value as a raw shell
    fragment the caller keeps safe).
    """

    packages: tuple[str, ...]
    extra_args: str | None = None
    venv: str | None = None

    def shell(self) -> str:
        parts = ["uv", "pip", "install", "--no-progress"]
        if self.venv is not None:
            parts.extend(["--python", shlex.quote(self.venv)])
        if self.extra_args:
            parts.append(self.extra_args)
        parts.extend(shlex.quote(package) for package in self.packages)
        return " ".join(parts)


@dataclass(frozen=True)
class UvPipUpgradeCommand(Command):
    """``uv pip install --upgrade --no-progress [--python <venv>] [extra_args] <packages>``: upgrade packages.

    The ``latest`` upgrade path of ``uv.packages`` -- pyinfra's
    ``upgrade_command``, built as the install command plus ``--upgrade`` so the
    extra arguments apply here too. Token order is uv's own: the flag sits
    between ``install`` and ``--no-progress``, and the environment selector and
    extra arguments ride exactly as they do on the install command.
    """

    packages: tuple[str, ...]
    extra_args: str | None = None
    venv: str | None = None

    def shell(self) -> str:
        parts = ["uv", "pip", "install", "--upgrade", "--no-progress"]
        if self.venv is not None:
            parts.extend(["--python", shlex.quote(self.venv)])
        if self.extra_args:
            parts.append(self.extra_args)
        parts.extend(shlex.quote(package) for package in self.packages)
        return " ".join(parts)


@dataclass(frozen=True)
class UvPipRemoveCommand(Command):
    """``uv pip uninstall --no-progress [--python <venv>] [extra_args] <packages>``: remove packages.

    The ``present=False`` direction of ``uv.packages`` (pyinfra's
    ``uninstall_command``). Unlike ``pip uninstall``, ``uv pip uninstall``
    needs no ``--yes`` -- uv is non-interactive by default -- so none is
    emitted. ``packages`` holds bare names, never ``==`` pins, and each is
    shell-quoted; the environment is named with ``--python`` (uv's uninstall
    accepts it) and the extra arguments ride here too (pyinfra builds the
    uninstall command with the same ``extras``).
    """

    packages: tuple[str, ...]
    extra_args: str | None = None
    venv: str | None = None

    def shell(self) -> str:
        parts = ["uv", "pip", "uninstall", "--no-progress"]
        if self.venv is not None:
            parts.extend(["--python", shlex.quote(self.venv)])
        if self.extra_args:
            parts.append(self.extra_args)
        parts.extend(shlex.quote(package) for package in self.packages)
        return " ".join(parts)


@dataclass(frozen=True)
class UvPipRequirementsCommand(Command):
    """``uv pip install [--upgrade] --no-progress [--python <venv>] [extra_args] -r <path>`` / ``uv pip uninstall --no-progress [--python <venv>] [extra_args] -r <path>``.

    The ``requirements`` direction of ``uv.packages``, one descriptor for both
    directions (the ``PipRequirementsCommand`` precedent). ``present=True``
    installs the file -- with ``--upgrade`` when ``latest`` asks for it,
    pyinfra's ``upgrade_command ... -r`` form; ``present=False`` removes every
    package the file names. The file path and the optional ``venv`` environment
    are shell-quoted and the extra arguments appended unquoted, as on the other
    uv pip descriptors.
    """

    path: str
    present: bool = True
    latest: bool = False
    extra_args: str | None = None
    venv: str | None = None

    def shell(self) -> str:
        if not self.present:
            parts = ["uv", "pip", "uninstall", "--no-progress"]
        else:
            parts = ["uv", "pip", "install"]
            if self.latest:
                parts.append("--upgrade")
            parts.append("--no-progress")
        if self.venv is not None:
            parts.extend(["--python", shlex.quote(self.venv)])
        if self.extra_args:
            parts.append(self.extra_args)
        parts.extend(["-r", shlex.quote(self.path)])
        return " ".join(parts)


async def packages(
    ctx: OpContext,
    packages: str | list[str] | tuple[str, ...] | None = None,
    present: bool = True,
    latest: bool = False,
    requirements: str | None = None,
    venv: str | None = None,
    extra_args: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Install, upgrade or remove Python packages with ``uv pip``.

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
        requirements (str | None, optional): Path of a requirements file on the
            host to install (or remove when ``present=False``); the file is
            applied on every run, the operation is stateless for it. Defaults
            to None.
        venv (str | None, optional): The environment to manage -- a virtual
            environment's root directory, an interpreter path or a version
            request -- named on the ``Packages`` fact read and on every command
            with ``--python``. Defaults to None, which leaves the environment to
            uv's own resolution; an empty value raises ``ValueError`` instead of
            silently retargeting.
        extra_args (str | None, optional): Free-form arguments appended
            (unquoted) to the install, upgrade and uninstall commands.
            Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated uv pip package commands.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/uv/uv_packages_op.py``.
    """

    # pyinfra ``uv.packages`` parity.
    #
    # The ``Packages`` fact supplies the installed versions, so a pinned
    # ``<pkg>==<version>`` is present only when that *exact* version is
    # installed (``ensure_packages``' rule, apt's/apk's/pip's), and ``latest``
    # follows pyinfra's legacy behavior: every installed, unpinned package is
    # upgraded with ``--upgrade`` without checking whether an upgrade exists.
    # pyinfra compares ``UvPipPackages`` keys lowercased, and the uv fact keeps
    # each distribution's printed (canonical) name verbatim with no PEP 503
    # folding, so a request and a fact entry meet at the *lowercased* name
    # here -- the deliberate difference from ``pip.packages``, which normalizes
    # ``-``/``_``/``.`` because ``pip freeze`` spells names inconsistently
    # while uv already prints the canonical one.
    #
    # The ``requirements`` option is stateless: like pyinfra, a given file
    # always yields its command and never compares against installed state,
    # and it is yielded *before* any package command, the order pyinfra emits.
    #
    # Deliberate deviations from pyinfra. Its ``extra_args`` accepted a
    # ``list``; reemote takes a single ``str`` appended unquoted (a list would
    # break the hashable-request rule when a caller passes it straight to
    # ``op()``, and one raw fragment expresses everything the list did).
    # pyinfra feeds ``uv pip uninstall`` the ``<pkg>==<version>`` tokens it was
    # given; reemote feeds *bare names*, matching what ``pip``'s uninstall
    # rejects pins on and what uv's own uninstall documents. And the whole
    # command family keeps pyinfra's ``--no-progress`` so uv never repaints a
    # progress bar into the captured stdout.
    #
    # uv installs into the environment it resolves (an active or discovered
    # ``.venv``, or the fallback interpreter) unless ``venv`` names one. The
    # option threads into ``Packages.command`` and onto every pip command as
    # ``--python``, so -- exactly as ``pip.packages`` threads its ``pip``
    # executable into ``PipPackages`` -- the fact always reports the environment
    # the commands mutate; scoping the commands alone would compare against one
    # environment while installing into another. uv accepts a virtual
    # environment's *root directory* there, not just a ``bin/python`` path. A
    # deploy can still steer resolution with the ``op()`` ``env`` prefix
    # (``VIRTUAL_ENV``, ``UV_PYTHON``), but a named target is first-class here.
    # An empty or blank ``venv`` is rejected up front -- the ``uv.venv``
    # precedent, and before the fact read so a bad request fails without an SSH
    # round trip -- because uv treats ``--python ''`` as unset and would
    # otherwise install into whatever environment it resolves.
    #
    # The install is per-user and unprivileged; only a *system* interpreter's
    # site-packages would need ``sudo=True``. ``**kwargs`` absorbs pyinfra's
    # global arguments (``op()`` already strips ``name``/``sudo``/``su``).
    if venv is not None and not venv.strip():
        raise ValueError("must provide the virtual environment to manage")

    reasons: list[str] = []

    # Requirements first, always: the direction is stateless, so the command is
    # yielded on every run whatever the environment holds.
    if requirements is not None:
        ctx.changed = True
        if present:
            reasons.append(f"requirements file {requirements} to install")
        else:
            reasons.append(f"requirements file {requirements} to remove")
        yield UvPipRequirementsCommand(
            path=requirements,
            present=present,
            latest=latest,
            extra_args=extra_args,
            venv=venv,
        )

    requested = _as_tuple(packages)

    if requested:
        installed = await ctx.facts.get(Packages, venv)
        versions_by_name: dict[str, str] = {
            entry["name"].lower(): entry["version"] for entry in installed
        }

        def is_installed(spec: str) -> bool:
            name, version = _split_pin(spec)
            got = versions_by_name.get(name.lower())
            if got is None:
                return False
            return version is None or version == got

        if present:
            to_install = tuple(pkg for pkg in requested if not is_installed(pkg))
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
                yield UvPipInstallCommand(
                    packages=to_install, extra_args=extra_args, venv=venv
                )
            if to_upgrade:
                ctx.changed = True
                reasons.append(f"{len(to_upgrade)} package(s) to upgrade")
                yield UvPipUpgradeCommand(
                    packages=to_upgrade, extra_args=extra_args, venv=venv
                )
            if not to_install and not to_upgrade:
                reasons.append("all packages present")
        else:
            # Version-aware (a pin is removed only when that version is
            # installed), but fed to uv as bare names.
            to_remove = tuple(
                dict.fromkeys(
                    _split_pin(pkg)[0] for pkg in requested if is_installed(pkg)
                )
            )
            if to_remove:
                ctx.changed = True
                reasons.append(f"{len(to_remove)} package(s) to remove")
                yield UvPipRemoveCommand(
                    packages=to_remove, extra_args=extra_args, venv=venv
                )
            else:
                reasons.append("no packages to remove")
    else:
        if not reasons:
            reasons.append("no packages requested")

    ctx.reason = "; ".join(reasons)


@dataclass(frozen=True)
class UvPythonInstallCommand(Command):
    """``uv python install --no-progress [extra_args] <version>``: install an interpreter.

    The ``present=True`` direction of ``uv.pythons`` (pyinfra's
    ``install_command``). uv installs one requested version per call, so the
    descriptor carries a single ``version`` token (``"3.13"``, ``"3.13.4"``),
    shell-quoted; ``extra_args`` is appended unquoted.
    """

    version: str
    extra_args: str | None = None

    def shell(self) -> str:
        parts = ["uv", "python", "install", "--no-progress"]
        if self.extra_args:
            parts.append(self.extra_args)
        parts.append(shlex.quote(self.version))
        return " ".join(parts)


@dataclass(frozen=True)
class UvPythonRemoveCommand(Command):
    """``uv python uninstall --no-progress [extra_args] <version>``: remove an interpreter.

    The ``present=False`` direction of ``uv.pythons`` (pyinfra's
    ``uninstall_command``). uv uninstalls one requested version per call, and
    the request token (which may be a partial ``"3.13"``) rides through
    shell-quoted exactly as it was given.
    """

    version: str
    extra_args: str | None = None

    def shell(self) -> str:
        parts = ["uv", "python", "uninstall", "--no-progress"]
        if self.extra_args:
            parts.append(self.extra_args)
        parts.append(shlex.quote(self.version))
        return " ".join(parts)


async def pythons(
    ctx: OpContext,
    versions: str | list[str] | tuple[str, ...] | None = None,
    present: bool = True,
    managed: bool = True,
    extra_args: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Install or remove uv-managed Python interpreter versions.

    Args:
        ctx (OpContext): Execution context for the operation.
        versions (str | list[str] | tuple[str, ...] | None, optional): Python
            version requests to ensure -- a major, ``major.minor`` or full
            ``major.minor.micro`` (``"3.13"``, ``"3.13.4"``). Prefer tuples via
            ``op()``. Defaults to None.
        present (bool, optional): Whether the versions should be installed.
            Defaults to True.
        managed (bool, optional): Whether only uv-managed interpreters (the
            default) or every interpreter uv knows about count as installed.
            Defaults to True.
        extra_args (str | None, optional): Free-form arguments appended
            (unquoted) to the install/uninstall command. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated uv python interpreter commands, one per version.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/uv/uv_pythons_op.py``.
    """

    # pyinfra ``uv.pythons`` parity.
    #
    # pyinfra compares its ``UvInstalledPythonsByVersion`` fact keyed on the
    # exact version string, so a request ``"3.13"`` never matches an installed
    # ``"3.13.4"`` and the version is reinstalled on every run -- a genuine
    # non-convergence. reemote compares dotted components instead (a request is
    # satisfied when its components are a prefix of some installed version), so
    # ``"3.13"`` is present once any ``3.13.*`` interpreter is installed and the
    # operation converges. uv itself resolves the same partial request.
    #
    # pyinfra's ``managed`` flag feeds ``--managed-python`` to the
    # install/uninstall commands *and* to the fact read. The fact deliberately
    # dropped that flag (it hides every system interpreter, so the answer
    # cannot carry the managed-versus-system split), so here ``managed`` is
    # purely the idempotence filter: ``managed=True`` counts only the entries
    # the ``Pythons`` fact flagged ``managed`` as installed, ``managed=False``
    # counts every interpreter present. The command itself installs a managed
    # download regardless, which is what uv does by default.
    #
    # One descriptor per version (uv takes one request at a time), the order
    # pyinfra emits. Installs/removals write to uv's own download directory and
    # are unprivileged. ``**kwargs`` absorbs pyinfra's global arguments.
    reasons: list[str] = []
    requested = _as_tuple(versions)

    if requested:
        installed = await ctx.facts.get(Pythons)
        candidates = {
            entry["version"] for entry in installed if entry["managed"] or not managed
        }

        def satisfied(version: str) -> bool:
            return any(
                _version_requested_matches(version, have) for have in candidates
            )

        for version in requested:
            if present:
                if satisfied(version):
                    reasons.append(f"python {version} already installed")
                else:
                    ctx.changed = True
                    reasons.append(f"python {version} to install")
                    yield UvPythonInstallCommand(version=version, extra_args=extra_args)
            else:
                if satisfied(version):
                    ctx.changed = True
                    reasons.append(f"python {version} to remove")
                    yield UvPythonRemoveCommand(version=version, extra_args=extra_args)
                else:
                    reasons.append(f"python {version} not installed")
    else:
        reasons.append("no Python versions requested")

    ctx.reason = "; ".join(reasons)


@dataclass(frozen=True)
class UvToolInstallCommand(Command):
    """``uv tool install --no-progress [--python <python>] [--with <pkg>]... [extra_args] <tool>``: install a tool.

    The ``present=True`` direction of ``uv.tools`` (pyinfra's
    ``install_command``). uv installs one tool per call, so the descriptor
    carries a single ``tool`` token -- a bare name or a ``<pkg>==<version>``
    pin -- shell-quoted. ``python`` names the interpreter the isolated tool
    environment is built from (``--python``) and ``with_packages`` its extra
    runtime requirements (one ``--with`` per entry, uv's repeatable flag);
    both are shell-quoted, and ``extra_args`` is appended unquoted after them.
    """

    tool: str
    extra_args: str | None = None
    python: str | None = None
    with_packages: tuple[str, ...] = ()

    def shell(self) -> str:
        parts = ["uv", "tool", "install", "--no-progress"]
        if self.python is not None:
            parts.extend(["--python", shlex.quote(self.python)])
        for package in self.with_packages:
            parts.extend(["--with", shlex.quote(package)])
        if self.extra_args:
            parts.append(self.extra_args)
        parts.append(shlex.quote(self.tool))
        return " ".join(parts)


@dataclass(frozen=True)
class UvToolUpgradeCommand(Command):
    """``uv tool install --upgrade --no-progress [--python <python>] [--with <pkg>]... [extra_args] <tool>``: upgrade a tool.

    The ``latest`` upgrade path of ``uv.tools`` -- pyinfra's
    ``upgrade_command``, the install command plus ``--upgrade`` (uv upgrades a
    tool by reinstalling it with the flag). Because the upgrade *re-creates*
    the tool environment, the requested interpreter and ``--with`` requirements
    ride here exactly as they do on the install command.
    """

    tool: str
    extra_args: str | None = None
    python: str | None = None
    with_packages: tuple[str, ...] = ()

    def shell(self) -> str:
        parts = ["uv", "tool", "install", "--upgrade", "--no-progress"]
        if self.python is not None:
            parts.extend(["--python", shlex.quote(self.python)])
        for package in self.with_packages:
            parts.extend(["--with", shlex.quote(package)])
        if self.extra_args:
            parts.append(self.extra_args)
        parts.append(shlex.quote(self.tool))
        return " ".join(parts)


@dataclass(frozen=True)
class UvToolRemoveCommand(Command):
    """``uv tool uninstall --no-progress [extra_args] <name>``: remove a tool.

    The ``present=False`` direction of ``uv.tools`` (pyinfra's
    ``uninstall_command``). ``name`` is the bare tool name (uv's uninstall
    takes a name, not a pinned requirement), shell-quoted; ``extra_args``
    rides here too. No ``--python``/``--with`` option exists on this verb, so
    the install-side environment options deliberately do not appear here.
    """

    name: str
    extra_args: str | None = None

    def shell(self) -> str:
        parts = ["uv", "tool", "uninstall", "--no-progress"]
        if self.extra_args:
            parts.append(self.extra_args)
        parts.append(shlex.quote(self.name))
        return " ".join(parts)


async def tools(
    ctx: OpContext,
    tools: str | list[str] | tuple[str, ...] | None = None,
    present: bool = True,
    latest: bool = False,
    python: str | None = None,
    with_packages: str | list[str] | tuple[str, ...] | None = None,
    extra_args: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Install, upgrade or remove isolated CLI tools with ``uv tool``.

    Args:
        ctx (OpContext): Execution context for the operation.
        tools (str | list[str] | tuple[str, ...] | None, optional): Tools to
            ensure. Prefer tuples via ``op()``. Versions can be pinned as
            ``<pkg>==<version>``. Defaults to None.
        present (bool, optional): Whether the tools should be installed.
            Defaults to True.
        latest (bool, optional): Whether to upgrade installed tools without a
            pinned version. Defaults to False.
        python (str | None, optional): Interpreter the tool's isolated
            environment is built from (``--python``) -- a version request or an
            interpreter path. Defaults to None, letting uv pick.
        with_packages (str | list[str] | tuple[str, ...] | None, optional): Extra
            runtime requirements for the tool environment, one ``--with`` each
            (``"sqlalchemy"``, ``"psycopg2-binary==2.9.9"``). Prefer tuples via
            ``op()``. Defaults to None.
        extra_args (str | None, optional): Free-form arguments appended
            (unquoted) to the install/upgrade/uninstall command. Defaults to
            None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated uv tool commands, one per tool.

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/uv/uv_tools_op.py``.
    """

    # pyinfra ``uv.tools`` parity.
    #
    # The ``Tools`` fact supplies the installed tool versions, so a pinned
    # ``<pkg>==<version>`` is present only when that exact version is installed
    # and a bare name when the tool is installed at any version -- pyinfra's
    # ``ensure_packages`` rule. A name is matched case-insensitively, as
    # ``uv.packages`` matches, because the fact keeps uv's printed spelling.
    # uv's ``tool install`` takes one package at a time (pyinfra's own note),
    # so one descriptor is yielded per tool rather than a batch, and
    # ``latest`` upgrades an installed, unpinned tool with ``uv tool install
    # --upgrade`` while a missing tool still installs.
    #
    # Deviation from pyinfra: its ``tool list`` fact discards a versionless
    # header (a tool installed from a local path or a git URL), so ``ensure_
    # packages`` saw it as absent and reinstalled forever; the reemote ``Tools``
    # fact keeps such a tool with ``version=None``, so a bare name matching an
    # installed-but-versionless tool is *present* and converges.
    #
    # No pyinfra counterpart for ``python``/``with_packages``: they declare the
    # tool's isolated environment (uv's ``--python`` and its repeatable
    # ``--with``) instead of selecting which tools exist. Both ride on the
    # install and the upgrade descriptor, where uv accepts them.
    #
    # Deliberate limitation, checked against the CLI: idempotence stays keyed on
    # name and version, because the fact is the plain ``uv tool list``, which
    # reports neither the interpreter nor the extra requirements (uv can print
    # them with ``--show-python``/``--show-with``, but that is not the gathered
    # command). A tool already installed at the requested version therefore does
    # NOT get reinstalled merely because ``python`` or ``with_packages`` changed;
    # converge on such drift with ``latest=True`` -- uv's upgrade re-creates the
    # environment, carrying these flags -- or by removing the tool first. Neither
    # flag exists on ``uv tool uninstall``, so the removal direction ignores them.
    #
    # Installing links the tool's executables into the user's uv tool bin dir;
    # the operation is unprivileged. ``**kwargs`` absorbs pyinfra's globals.
    reasons: list[str] = []
    requested = _as_tuple(tools)
    with_requested = _as_tuple(with_packages)

    if requested:
        installed = await ctx.facts.get(Tools)
        # Membership, not a truthy version: a tool installed from a local path
        # or git URL carries ``version=None`` yet is genuinely installed, so the
        # name's presence in the map -- not its version value -- is the test.
        versions_by_name: dict[str, str | None] = {
            entry["name"].lower(): entry["version"] for entry in installed
        }

        def is_present(token: str) -> bool:
            name, version = _split_pin(token)
            key = name.lower()
            if key not in versions_by_name:
                return False
            got = versions_by_name[key]
            return version is None or version == got

        for token in requested:
            name = _split_pin(token)[0]
            if present:
                if not is_present(token):
                    ctx.changed = True
                    reasons.append(f"tool {name} to install")
                    yield UvToolInstallCommand(
                        tool=token,
                        extra_args=extra_args,
                        python=python,
                        with_packages=with_requested,
                    )
                elif latest and _split_pin(token)[1] is None:
                    ctx.changed = True
                    reasons.append(f"tool {name} to upgrade")
                    yield UvToolUpgradeCommand(
                        tool=token,
                        extra_args=extra_args,
                        python=python,
                        with_packages=with_requested,
                    )
                else:
                    reasons.append(f"tool {name} already installed")
            else:
                if is_present(token):
                    ctx.changed = True
                    reasons.append(f"tool {name} to remove")
                    yield UvToolRemoveCommand(name=name, extra_args=extra_args)
                else:
                    reasons.append(f"tool {name} not installed")
    else:
        reasons.append("no tools requested")

    ctx.reason = "; ".join(reasons)


@dataclass(frozen=True)
class UvToolUpgradeAllCommand(Command):
    """``uv tool upgrade --all [extra_args]``: upgrade every installed tool.

    pyinfra's ``tools_upgrade_all`` command verbatim (less its ``requires_command``
    gate). Stateless -- uv decides per tool whether an upgrade exists -- so
    ``extra_args`` rides unquoted at the end.
    """

    extra_args: str | None = None

    def shell(self) -> str:
        parts = ["uv", "tool", "upgrade", "--all"]
        if self.extra_args:
            parts.append(self.extra_args)
        return " ".join(parts)


async def tools_upgrade_all(
    ctx: OpContext,
    extra_args: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Upgrade every installed ``uv`` tool to its latest version.

    Args:
        ctx (OpContext): Execution context for the operation.
        extra_args (str | None, optional): Free-form arguments appended
            (unquoted) to the command. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The upgrade command, always (the operation is stateless).

    **Raises:**

    ```python
    None
    ```

    Examples:
        Examples can be found in ``examples/modules/uv/uv_tools_upgrade_all.py``.
    """

    # pyinfra ``uv.tools_upgrade_all`` parity: a non-idempotent, fact-free
    # operation -- it never compares state, so it always yields and always
    # reports a change. There is no environment/interpreter read, so nothing on
    # the generator's own path raises (the ``None`` in the **Raises:** fence);
    # a missing uv surfaces as ``CommandNotFoundError`` at apply time, which the
    # contract excludes. ``**kwargs`` absorbs pyinfra's globals.
    ctx.changed = True
    ctx.reason = "upgrade all uv tools"
    yield UvToolUpgradeAllCommand(extra_args=extra_args)


@dataclass(frozen=True)
class UvToolUpdateShellCommand(Command):
    """``uv tool update-shell``: add the uv tool bin directory to the PATH.

    pyinfra's ``tools_update_shell`` command verbatim. It takes no path (uv
    infers its own bin directory), so the descriptor is a fixed shell; uv edits
    the user's shell configuration, an unprivileged change.
    """

    def shell(self) -> str:
        return "uv tool update-shell"


async def tools_update_shell(
    ctx: OpContext,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Ensure uv's tool executable directory is on the ``PATH``.

    Args:
        ctx (OpContext): Execution context for the operation.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The update-shell command, only when the bin dir is missing
            from the ``PATH`` (the operation is idempotent on the facts).

    **Raises:**

    ```python
    CommandNotFoundError, HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/uv/uv_tools_update_shell.py``.
    """

    # pyinfra ``uv.tools_update_shell`` parity: it compares its ``UvToolDir``
    # fact against the ``server.Path`` (``$PATH``) fact and yields
    # ``uv tool update-shell`` only when the directory is absent. reemote reads
    # the tool bin directory from the consolidated ``Info`` fact's
    # ``tool_bin_dir`` (pyinfra's ``UvToolDir`` is ``uv tool dir``; the
    # executables live under ``uv tool dir --bin``, which ``Info`` resolves)
    # and the ``PATH`` from ``server.Env("PATH")`` -- reemote's generalization
    # of pyinfra's ``Path`` fact. ``Env`` splits nothing, so the operation
    # colon-splits the raw value itself, matching pyinfra's
    # ``.split(':')`` membership test.
    #
    # When uv is not installed the ``Info`` chain short-circuits and
    # ``tool_bin_dir`` is ``None``: an unknown directory is treated as "nothing
    # to add" (no shell change), not a perpetual re-yield. ``**kwargs`` absorbs
    # pyinfra's globals; the update-shell command is unprivileged.
    reasons: list[str] = []
    info = await ctx.facts.get(Info)
    tool_bin_dir = info["tool_bin_dir"]
    path = await ctx.facts.get(Env, "PATH")

    if not tool_bin_dir:
        reasons.append("uv tool bin directory unknown, shell config left alone")
    elif tool_bin_dir in path.split(":"):
        reasons.append(f"{tool_bin_dir} is already on the PATH")
    else:
        ctx.changed = True
        reasons.append(f"{tool_bin_dir} is not on the PATH, will update it")
        yield UvToolUpdateShellCommand()

    ctx.reason = "; ".join(reasons)


class _VenvExists:
    # Private, module-local probe: whether a uv virtualenv has been created at
    # ``path`` (``uv venv <path>`` writes ``<path>/bin/activate``). The
    # existence counterpart of pip's ``_VirtualenvPython`` -- here the question
    # is only *is there a venv*, not which interpreter runs it, so pyinfra's
    # uv.venv existence rule (a ``files.File`` fact on ``bin/activate``) is
    # restated as a single guarded, always-exit-0 shell rather than reaching
    # into the files namespace. Kept private and uv-local on purpose.
    #
    # Only ``HostConnectionError`` escapes: the ``|| true`` guard swallows the
    # failed ``test``, so an absent venv (the normal first run) reads False
    # instead of raising ``CommandNotFoundError``.

    @classmethod
    def command(cls, path: str) -> str:
        """Guarded shell asking whether ``<path>/bin/activate`` exists.

        ``test -e`` and the ``echo yes`` run inside one ``|| true`` group, so
        the command always exits 0 and prints ``yes`` only when the venv is
        there. The path is ``shlex``-quoted.
        """
        activate = f"{shlex.quote(path)}/bin/activate"
        return f"test -e {activate} && echo yes || true"

    @classmethod
    def parse(cls, output: Iterable[str]) -> bool:
        """Whether the probe printed its ``yes`` token. Pure and testable."""
        return any(line.strip() == "yes" for line in output)

    @classmethod
    async def process(cls, host: Host, path: str) -> bool:
        venvs = getattr(host, "uv_venvs", None)
        if venvs is not None:
            # SimulatedHost seam: a set of paths that hold a uv venv; an
            # absent path is simply no virtualenv there, not an error.
            return path in venvs
        # Real transport: the guarded probe over asyncssh.
        stdout = await host.run_shell(cls.command(path))
        return cls.parse(stdout.splitlines())


@dataclass(frozen=True)
class UvVenvCreateCommand(Command):
    """``uv venv [flags] [extra_args] <path>``: create a virtualenv.

    pyinfra's uv venv builder. Flags render in pyinfra's own order --
    ``--allow-existing``/``--clear`` (mutually exclusive, validated by the
    operation), ``--seed``, ``--link-mode <mode>``, ``--site-packages``,
    ``--python <interpreter>`` -- then the unquoted ``extra_args``, then the
    path. Every user string (interpreter, link mode, path) is ``shlex``-quoted
    so a metacharacter cannot break out of the argument.
    """

    path: str
    python: str | None = None
    seed: bool = False
    allow_existing: bool = False
    clear_existing: bool = False
    link_mode: str | None = None
    site_packages: bool = False
    extra_args: str | None = None

    def shell(self) -> str:
        parts = ["uv", "venv"]
        if self.allow_existing:
            parts.append("--allow-existing")
        if self.clear_existing:
            parts.append("--clear")
        if self.seed:
            parts.append("--seed")
        if self.link_mode:
            parts.extend(["--link-mode", shlex.quote(self.link_mode)])
        if self.site_packages:
            parts.append("--site-packages")
        if self.python:
            parts.extend(["--python", shlex.quote(self.python)])
        if self.extra_args:
            parts.append(self.extra_args)
        parts.append(shlex.quote(self.path))
        return " ".join(parts)


@dataclass(frozen=True)
class UvVenvRemoveCommand(Command):
    """``rm -rf <path>``: remove a virtualenv tree.

    The ``present=False`` direction of ``uv.venv`` -- the whole-tree removal
    pyinfra performs by delegating to ``files.directory(present=False)``,
    restated as a uv-owned descriptor because operations do not cross into
    another namespace (the ``PipVenvRemoveCommand`` precedent). The path is
    ``shlex``-quoted.
    """

    path: str

    def shell(self) -> str:
        return f"rm -rf {shlex.quote(self.path)}"


async def venv(
    ctx: OpContext,
    path: str,
    present: bool = True,
    python: str | None = None,
    allow_existing: bool = False,
    clear_existing: bool = False,
    link_mode: str | None = None,
    seed: bool = False,
    site_packages: bool = False,
    extra_args: str | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Create or remove a ``uv venv`` virtualenv.

    Args:
        ctx (OpContext): Execution context for the operation.
        path (str): Filesystem location of the virtualenv.
        present (bool, optional): Whether the venv should exist. Defaults to
            True.
        python (str | None, optional): Base interpreter to build the venv from
            (``--python``). None lets uv pick its default. Defaults to None.
        allow_existing (bool, optional): Allow and retain existing files in the
            venv directory (``--allow-existing``). Mutually exclusive with
            ``clear_existing``. Defaults to False.
        clear_existing (bool, optional): Remove all existing files in the venv
            directory before creating (``--clear``). Mutually exclusive with
            ``allow_existing``. Defaults to False.
        link_mode (str | None, optional): How uv places files into the venv --
            ``"clone"``, ``"copy"``, ``"hardlink"`` or ``"symlink"``
            (``--link-mode``). None uses uv's platform default. Defaults to
            None.
        seed (bool, optional): Seed the venv with ``pip`` (and, pre-3.12,
            ``setuptools``/``wheel``) (``--seed``). Defaults to False.
        site_packages (bool, optional): Give the venv access to the global
            site-packages (``--site-packages``). Defaults to False.
        extra_args (str | None, optional): Free-form arguments appended
            (unquoted) to the command. Defaults to None.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated uv venv commands.

    **Raises:**

    ```python
    HostConnectionError, ValueError
    ```

    Examples:
        Examples can be found in ``examples/modules/uv/uv_venv.py``.
    """

    # pyinfra ``uv.venv`` parity.
    #
    # Idempotence is existence-only, exactly as pyinfra builds it (a probe on
    # the venv, then create-if-missing / remove-if-present): a venv that exists
    # is converged, and unlike ``pip.virtualenv`` there is no interpreter-
    # version reconcile (uv's ``venv`` is cheap to recreate and pyinfra asks no
    # such question). Deliberate deviations from pyinfra. Its existence check
    # keys on ``<path>/.venv/bin/activate`` yet it creates the venv *at*
    # ``<path>`` with ``uv venv <path>`` -- a path mismatch (upstream bug).
    # reemote treats ``path`` as the venv directory itself throughout: the
    # probe asks for ``<path>/bin/activate``, ``uv venv`` creates at ``<path>``
    # and ``present=False`` removes ``<path>``, one consistent location.
    #
    # Removal (``present=False``) is the whole-tree ``rm -rf`` pyinfra
    # delegates to ``files.directory(present=False)``, kept uv-local. The two
    # option validations pyinfra raises -- ``allow_existing`` together with
    # ``clear_existing``, and an empty ``path`` -- are checked before the fact
    # read so a bad request fails without an SSH round trip (pyinfra checks
    # them inside the ``present`` branch, so a ``present=False`` bad request
    # silently skipped them; reemote rejects them always). Creating a venv in
    # the user's own path is unprivileged; a root-owned parent would need
    # ``sudo=True``. ``**kwargs`` absorbs pyinfra's globals.
    if allow_existing and clear_existing:
        raise ValueError("allow_existing and clear_existing cannot both be set")
    if not str(path).strip():
        raise ValueError("must provide the path in which the venv is to be created")

    reasons: list[str] = []
    exists = await ctx.facts.get(_VenvExists, path)
    if present:
        if exists:
            reasons.append(f"venv already exists at {path}")
        else:
            ctx.changed = True
            reasons.append(f"venv {path} missing, will create")
            yield UvVenvCreateCommand(
                path=path,
                python=python,
                seed=seed,
                allow_existing=allow_existing,
                clear_existing=clear_existing,
                link_mode=link_mode,
                site_packages=site_packages,
                extra_args=extra_args,
            )
    else:
        if exists:
            ctx.changed = True
            reasons.append(f"venv {path} present, will remove")
            yield UvVenvRemoveCommand(path=path)
        else:
            reasons.append(f"venv does not exist at {path}")

    ctx.reason = "; ".join(reasons)


__all__ = [
    "Info",
    "InstallInfo",
    "PackageInfo",
    "Packages",
    "PythonInfo",
    "Pythons",
    "ToolInfo",
    "Tools",
    "UvPipInstallCommand",
    "UvPipRemoveCommand",
    "UvPipRequirementsCommand",
    "UvPipUpgradeCommand",
    "UvPythonInstallCommand",
    "UvPythonRemoveCommand",
    "UvToolInstallCommand",
    "UvToolRemoveCommand",
    "UvToolUpdateShellCommand",
    "UvToolUpgradeAllCommand",
    "UvToolUpgradeCommand",
    "UvVenvCreateCommand",
    "UvVenvRemoveCommand",
    "packages",
    "pythons",
    "tools",
    "tools_update_shell",
    "tools_upgrade_all",
    "venv",
]
