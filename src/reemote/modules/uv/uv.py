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
# tool. Like every fact it is phase-unaware: its ``process`` coroutine just
# gathers the raw value.
#
# Operation side: pyinfra's ``uv.packages`` operation is not ported yet, so
# this namespace currently ships the four facts alone. When the operation
# lands it reads ``Packages`` for its idempotence, the ``pip``/``npm``/``gem``
# precedent: operations are async generators that receive an ``OpContext``,
# await facts, set ``ctx.changed``/``ctx.reason``, and yield command
# descriptors; they never touch the host directly.


import json
import posixpath
from collections.abc import Iterable
from typing import ClassVar, TypedDict

from ...hosts import Host

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
    async def process(cls, host: Host) -> list[PackageInfo]:
        uv_packages = getattr(host, "uv_packages", None)
        if uv_packages is not None:
            # SimulatedHost seam: the store is one mapping of printed name to
            # version -- one environment, whatever uv resolves -- keyed
            # exactly as ``parse`` keys its answer, so simulated state uses
            # the verbatim uv spelling. Copy each entry so callers never
            # mutate simulated state.
            return [
                {"name": name, "version": version}
                for name, version in sorted(uv_packages.items())
            ]
        # Real transport: the JSON listing over asyncssh. A non-zero exit
        # that is not 127 (uv failing to resolve an environment, say) leaves
        # stdout empty, which parses as [].
        stdout = await host.run_shell(cls.COMMAND)
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


__all__ = [
    "Info",
    "InstallInfo",
    "PackageInfo",
    "Packages",
    "PythonInfo",
    "Pythons",
    "ToolInfo",
    "Tools",
]
