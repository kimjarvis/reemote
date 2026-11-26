# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# The ``go`` module: facts and operations in one source.
#
# Fact side: ``GoPackages`` reports the Go binaries installed via
# ``go install`` as ``GoPackageInfo`` entries (the binary's main-package
# import path plus one module version), parsed from the ``go version -m``
# rows of the executable listing of the go bin directory. ``GoVersion``
# reports the active toolchain's version and default target platform, ``GoEnv``
# the effective ``go env`` configuration, ``GoInstalledSDKs`` the side-by-side
# SDK binaries under a set of search directories, ``GoModuleInfo`` the go.mod
# metadata of a module directory on the host, and ``GoBinaryBuildInfo`` the
# build metadata embedded in a compiled binary. Like every fact they are
# phase-unaware: their ``process`` coroutines just gather the raw value.
#
# Operation side: ``go.packages`` installs, re-installs or removes those
# binaries -- bare import paths or ``<pkg>=<version>`` pins -- through its
# ``GoInstallCommand``/``GoRemoveCommand`` descriptors. ``go.env`` persists or
# removes ``go env`` settings (``GoEnvSetCommand``/``GoEnvUnsetCommand``),
# ``go.cache_clean`` empties the build and/or module cache
# (``GoCleanCommand``, stateless), and ``go.build`` compiles a package into a
# binary (``GoBuildCommand``) -- the latter three all shell out as the
# connecting user and read the facts above for idempotence. Operations are
# async generators that receive an ``OpContext``, await facts, set
# ``ctx.changed``/``ctx.reason``, and yield command descriptors; they never
# touch the host directly.

import json
import re
import shlex
from collections.abc import AsyncGenerator, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, TypedDict

from ...commands import Command
from ...core import OpContext
from ...hosts import Host

# --- Shared helpers ----------------------------------------------------------


def _split_version(package: str) -> tuple[str, str | None]:
    """Split ``pkg=version`` into ``(name, version)``; version is None if absent.

    The spelling is reemote's one user-facing pin format, shared with
    ``apt``/``dnf``/``apk``/``npm``/``gem`` (whose ``_split_version`` this is
    the copy of): split at the *last* ``=`` so a version string carrying
    ``=`` stays whole, and return both halves verbatim. A go import path
    carries no ``=``, so the split is unambiguous. Pure and testable.
    """
    name, sep, version = package.rpartition("=")
    return (name, version) if sep else (package, None)


def _go_package_spec(package: str) -> str:
    """Translate a ``<pkg>=<version>`` request into go's ``<pkg>@<version>``.

    The user-facing pin is reemote's shared ``=`` spelling; go's own
    requirement token joins path and version with ``@`` -- the ``npm``
    translation precedent. An unpinned name and the special ``latest`` pin
    both request the newest module and render as ``<pkg>@latest``, the token
    pyinfra builds for them (a bare ``go install <path>`` refuses to run
    outside a module, so ``@latest`` is never omitted). Pure and testable.
    """
    name, version = _split_version(package)
    if version is None or version == "latest":
        return f"{name}@latest"
    return f"{name}@{version}"


def _go_split_spec(token: str) -> tuple[str, str | None]:
    """Split go's ``<pkg>@<version>`` token into ``(path, version)``.

    The inverse of ``_go_package_spec`` for the ``SimulatedHost`` seam,
    splitting at the *first* ``@`` -- pyinfra's own ``partition("@")`` rule --
    because a go import path carries no ``@``, unlike npm where a scoped name
    begins with one. A bare path yields no version. Pure and testable.
    """
    name, sep, version = token.partition("@")
    return (name, version) if sep else (token, None)


def _binary_name(pkg_path: str) -> str:
    """Return the basename of the binary produced by ``go install <pkg_path>``.

    Under Go's Semantic Import Versioning, modules at major version v2+ carry
    a ``/v<N>`` suffix on their import path that is not part of the binary
    name -- ``github.com/foo/bar/v2`` installs a binary named ``bar``. Two
    distinct import paths can still map to the same binary name (e.g.
    ``a/b/cmd/tool`` and ``x/y/cmd/tool``), mirroring how ``go install``
    itself clobbers one binary with the other; on removal ``rm -f`` cannot
    tell them apart either. Pyinfra's builder copied verbatim.
    """
    parts = pkg_path.split("/")
    if len(parts) >= 2 and re.fullmatch(r"v\d+", parts[-1]):
        return parts[-2]
    return parts[-1]


# --- Facts -------------------------------------------------------------------


class GoPackageInfo(TypedDict):
    """
    Attributes:
        path: The binary's main-package import path exactly as the module
            records it (``"github.com/bitmagnet-io/bitmagnet"``).
        version: One module version the binary was built at (``"v0.9.0"``).
            A path rebuilt at several versions -- or shared by several
            binaries -- contributes one entry per version.
    """

    # One installed Go binary's module at one version -- the entry of the
    # ``GoPackages`` fact. A TypedDict rather than a class instance: the
    # answer is an ordinary path/version pair, so equality against a literal
    # dict works and ``json.dumps`` serializes it directly -- the
    # ``GemPackageInfo``/``NpmPackageInfo`` convention of every package-list
    # fact in this codebase. ``path`` rather than ``name`` because go keys
    # tools by their full import path, not a short name. A path can hold
    # several versions (``gem``'s precedent, not npm's one-per-name) when
    # two binaries in the go bin directory carry the same main module built
    # at different versions.

    path: str
    version: str


class GoPackages:
    """The ``go install`` binaries, as ``GoPackageInfo`` entries.

    **Commands:**

    ```bash
    BINDIR=$(go env GOBIN); [ -z "$BINDIR" ] && BINDIR="$(go env GOPATH)/bin"; ! test -d "$BINDIR" || find "$BINDIR" -maxdepth 1 -type f -perm -u+x -exec go version -m {} + 2>/dev/null
    ```

    **Returns:**

    ```python
    list[GoPackageInfo]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/go/go_go_packages.py``.
    """

    # Mirrors pyinfra's ``go.GoPackages``: the same compound command resolves
    # the go bin directory (``GOBIN``, else ``$GOPATH/bin``), and -- only when
    # that directory exists -- runs ``go version -m`` over every regular
    # executable in it; ``parse`` then keeps each binary's ``path`` row paired
    # with its ``mod`` row's version. ``dep``/``build`` rows, the
    # ``<binary>: go1.x`` header lines and blank lines are ignored, and a
    # ``(devel)`` version (``go build`` output, not ``go install``) is skipped
    # so only versioned installs are reported. pyinfra returns a
    # ``dict[str, set[str]]`` keyed by import path; reemote reports sorted,
    # unique ``GoPackageInfo`` entries -- the deterministic, JSON-printable
    # answer shape every package-list fact here uses (the ``GemPackages``
    # precedent).
    #
    # pyinfra's ``requires_command = "go"`` gate is deliberately dropped, but
    # unlike the bare-command facts (``gem list``, where a missing tool exits
    # 127 as ``CommandNotFoundError``) there is nothing to hear about here:
    # the command is self-guarding end to end. A missing ``go`` fails only
    # inside command substitutions and ``find -exec`` (whose stderr the
    # ``2>/dev/null`` hides and whose failure ``find`` reports as exit 1,
    # never 127), and a missing bin directory short-circuits the listing to
    # exit 0 -- so fact gathering, which tolerates non-zero exits, reads
    # unparsable silence as ``[]``. That is exactly the answer pyinfra's
    # ``default = dict`` gate produces, reached without the gate.

    #: Shell used to gather the value over SSH (pyinfra's command verbatim).
    COMMAND = (
        'BINDIR=$(go env GOBIN); [ -z "$BINDIR" ] && '
        'BINDIR="$(go env GOPATH)/bin"; ! test -d "$BINDIR" || '
        'find "$BINDIR" -maxdepth 1 -type f -perm -u+x '
        "-exec go version -m {} + 2>/dev/null"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> list[GoPackageInfo]:
        """Parse ``go version -m`` rows into sorted, unique entries.

        Tab-indented rows carry one record each: ``path`` names the binary's
        main package and the versioned ``mod`` row that follows contributes
        the ``(path, version)`` pair (``dep`` and ``build`` rows match no key
        and are ignored). A row not starting with a tab -- the per-binary
        ``<file>: go1.x`` header -- closes the current path, so a ``mod`` row
        never attaches to the previous binary's ``path``; a ``mod`` row whose
        version is ``(devel)`` (a ``go build`` artifact) is skipped, and
        output with no complete pair reads as ``[]``. Entries are
        de-duplicated and sorted by ``(path, version)``. Pure and testable.
        """
        rows: set[tuple[str, str]] = set()
        current_path: str | None = None
        for line in output:
            if not line.startswith("\t"):
                current_path = None
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            key = parts[1]
            if key == "path":
                current_path = parts[2]
            elif key == "mod" and current_path is not None and len(parts) >= 4:
                version = parts[3]
                # ``(devel)`` is ``go build`` output, not ``go install`` --
                # skip it so the fact only reports versioned installs.
                if version != "(devel)":
                    rows.add((current_path, version))
                current_path = None
        return [{"path": path, "version": version} for path, version in sorted(rows)]

    @classmethod
    async def process(cls, host: Host) -> list[GoPackageInfo]:
        go_packages = getattr(host, "go_packages", None)
        if go_packages is not None:
            # SimulatedHost seam: the import path -> installed-version-list
            # store, projected into sorted entries. Freshly built dicts so
            # callers never mutate simulated state.
            return [
                {"path": path, "version": version}
                for path, versions in sorted(go_packages.items())
                for version in sorted(set(versions))
            ]
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class GoVersionInfo(TypedDict):
    """
    Attributes:
        version: The toolchain version with its ``go`` prefix stripped
            (``"1.23.4"``). ``""`` when the version line carries no
            ``go``-prefixed field (a ``devel`` build reports ``devel`` there).
        raw: The full ``go version`` output line verbatim
            (``"go version go1.23.4 linux/amd64"``).
        goos: The default target OS from ``go env GOOS`` (``"linux"``).
        goarch: The default target architecture from ``go env GOARCH``
            (``"amd64"``).
    """

    # The answer shape of the ``GoVersion`` fact -- one toolchain, four
    # fields. A TypedDict rather than a class instance, the ``GoPackageInfo``
    # convention: the answer is an ordinary field bag, so equality against a
    # literal dict works and ``json.dumps`` serializes it directly.

    version: str
    raw: str
    goos: str
    goarch: str


class GoVersion:
    """The active Go toolchain's version and default target platform.

    **Commands:**

    ```bash
    command -v go >/dev/null 2>&1 || exit 0; go version && go env GOOS GOARCH
    ```

    **Returns:**

    ```python
    GoVersionInfo | None
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/go/go_go_version.py``.
    """

    # Adapted from the proposed ``GoVersion`` design, which paired a
    # ``requires_command = "go"`` gate with ``default = dict``. reemote drops
    # the gate (the established decision every go fact here follows), and --
    # like the self-guarding ``GoPackages`` command -- the guard lives in the
    # shell instead: ``command -v go || exit 0`` short-circuits a host without
    # the toolchain to empty output that parses to absence, never to exit 127
    # and ``CommandNotFoundError``. The proposal reported absence as ``{}``;
    # a single-valued fact answers ``None`` here (the ``Which`` precedent), so
    # callers can branch on the answer directly.
    #
    # ``go env GOOS GOARCH`` with positional arguments prints the two BARE
    # values (one per line, no ``KEY=`` prefix -- unlike the plain ``go env``
    # listing) and ``go version`` one header line, so the parser reads lines
    # positionally: the third field of the header is the version (its ``go``
    # prefix stripped, the proposal's rule) and lines two and three are
    # GOOS/GOARCH. Fewer than three non-blank lines is no answer at all.

    #: Shell used to gather the value over SSH.
    COMMAND = (
        "command -v go >/dev/null 2>&1 || exit 0; "
        "go version && go env GOOS GOARCH"
    )

    @classmethod
    def parse(cls, output: Iterable[str]) -> GoVersionInfo | None:
        """Parse the version header plus two env lines; short output is None.

        The first non-blank line is the ``go version`` output (its third
        field, ``go``-prefixed, is the version), the next two the bare
        ``GOOS`` and ``GOARCH`` values. A header whose version field carries
        no ``go`` prefix (a ``devel`` build) still reports an answer with an
        empty ``version`` -- the raw line and the target are known. Pure and
        testable.
        """
        lines = [line.strip() for line in output if line.strip()]
        if len(lines) < 3:
            return None
        raw = lines[0]
        parts = raw.split()
        version = ""
        if len(parts) >= 3 and parts[2].startswith("go"):
            version = parts[2].removeprefix("go")
        return {
            "version": version,
            "raw": raw,
            "goos": lines[1],
            "goarch": lines[2],
        }

    @classmethod
    async def process(cls, host: Host) -> GoVersionInfo | None:
        go_version = getattr(host, "go_version", None)
        if go_version is not None:
            # SimulatedHost seam: the version/goos/goarch store, copied so
            # callers never mutate simulated state. An unseeded (empty) store
            # is a host without the toolchain, reported None like a guarded
            # empty shell read.
            return dict(go_version) if go_version else None
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.COMMAND)
        return cls.parse(stdout.splitlines())


class GoEnv:
    """The effective ``go env`` configuration, as a KEY -> value mapping.

    **Commands:**

    ```bash
    command -v go >/dev/null 2>&1 || exit 0; go env
    ```

    **Returns:**

    ```python
    dict[str, str]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/go/go_go_env.py``.
    """

    # Adapted from the proposed ``GoEnv`` design: the same KEY='VALUE' reader
    # and the same self-guarded absence reading as the sibling go facts (a
    # host without go answers ``{}``, the proposal's ``default = dict``).
    #
    # The proposal's filtered form has a shell bug: ``go env <KEY>...`` with
    # positional arguments prints the bare VALUES, one per line, with no
    # ``KEY=`` prefix -- unparseable back into a mapping by any KEY= reader.
    # A filtered read therefore runs the full listing and narrows it in
    # ``process``, which also keeps the shell (and the cache key) independent
    # of the key list.
    #
    # ``go env`` single-quotes every value (``GOFLAGS=''``, ``GOPATH='/x'``);
    # ``parse`` strips one matched outer quote pair. ``GOPROXY`` values
    # legitimately carry commas and ``GOFLAGS`` spaces -- the parser is
    # split-on-first-``=`` only, so nothing else is touched.

    #: Shell used to gather the value over SSH (always the full listing; a
    #: filtered read narrows its parse in ``process``).
    COMMAND = "command -v go >/dev/null 2>&1 || exit 0; go env"

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, str]:
        """Parse ``KEY='VALUE'`` rows into the mapping.

        Lines without an ``=`` contribute nothing (the listing is the only
        expected output, so stray noise is skipped the way every sibling
        reader skips it), and one matched outer pair of single or double
        quotes is stripped from each value -- an unquoted value is kept
        verbatim. Pure and testable.
        """
        env: dict[str, str] = {}
        for line in output:
            line = line.strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            env[key.strip()] = value
        return env

    @classmethod
    async def process(
        cls, host: Host, keys: Sequence[str] | None = None
    ) -> dict[str, str]:
        state = getattr(host, "go_env", None)
        if state is not None:
            # SimulatedHost seam: the variable -> value store. A fresh dict
            # so callers never mutate simulated state; an unseeded store
            # answers {} -- the simulated machine reports no go configuration
            # until a test names it.
            env = dict(state)
        else:
            # Real transport: gather over asyncssh from the host's connection.
            stdout = await host.run_shell(cls.COMMAND)
            env = cls.parse(stdout.splitlines())
        if keys is None:
            return env
        # A filtered read keeps the requested order and drops unknown keys --
        # an unset variable simply has no entry, exactly like the full read.
        return {key: env[key] for key in keys if key in env}


class GoInstalledSDKs:
    """Side-by-side installed Go SDKs, as version -> binary path.

    **Commands:**

    ```bash
    for d in /usr/local /opt; do for b in "$d"/go*/bin/go "$d"/*/bin/go; do [ -x "$b" ] && printf '%s\\t%s\\n' "$b" "$("$b" version 2>/dev/null)"; done; done
    ```

    **Returns:**

    ```python
    dict[str, str]
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/go/go_go_installed_sdks.py``.
    """

    # Adapted from the proposed ``GoInstalledSDKs`` design. The proposal's
    # shell loop was mis-quoted (its ``echo "$bin"$'\\t'`` fragment cannot
    # parse); this is the equivalent POSIX loop: for each search directory,
    # every executable ``bin/go`` under a ``go*``-prefixed or any other
    # subdirectory prints ``<path>TAB<version line>``. No toolchain gate is
    # needed -- the loop never invokes ``go`` itself, only the SDK binaries it
    # finds, and a host with none prints nothing and reads ``{}`` (matching
    # the proposal, which declared no ``requires_command`` either). Unmatched
    # globs stay literal and fail the ``[ -x ]`` test, so absent directories
    # contribute nothing.
    #
    # One path matches both patterns (``/usr/local/go/bin/go`` is also
    # ``*/bin/go``) and lands twice; the version -> path mapping collapses
    # that to one entry. Two SDKs reporting the same version share the key --
    # the later scan wins, the proposal's mapping rule (version is identity
    # here, path is the answer). A ``devel`` build reports ``devel`` where the
    # version field belongs and contributes nothing.

    #: Directories scanned when the caller names none: the tarball install
    #: locations (``/usr/local/go``, versioned ``/usr/local/go1.x`` copies,
    #: ``/opt`` installs), the proposal's defaults.
    DEFAULT_SEARCH_DIRS = ("/usr/local", "/opt")

    @classmethod
    def command(cls, search_dirs: Sequence[str] = DEFAULT_SEARCH_DIRS) -> str:
        """Shell looping the search dirs and printing each SDK's version.

        Every directory is ``shlex``-quoted, so a search path carrying shell
        metacharacters is scanned literally rather than executed.
        """
        dirs = " ".join(shlex.quote(directory) for directory in search_dirs)
        return (
            f'for d in {dirs}; do for b in "$d"/go*/bin/go "$d"/*/bin/go; '
            "do "
            '[ -x "$b" ] && '
            "printf '%s\\t%s\\n' \"$b\" \"$(\"$b\" version 2>/dev/null)\"; "
            "done; done"
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> dict[str, str]:
        """Parse ``<path>TAB<go version line>`` rows into version -> path.

        Rows without a tab contribute nothing; the version is the third
        field of the printed ``go version`` line (``go1.22.10`` ->
        ``"1.22.10"``, the ``GoVersion`` strip rule) and an empty or devel
        version line yields no entry. Later rows win an already-claimed
        version key. Pure and testable.
        """
        sdks: dict[str, str] = {}
        for line in output:
            path, sep, version_line = line.partition("\t")
            if not sep:
                continue
            parts = version_line.split()
            if len(parts) >= 3 and parts[2].startswith("go"):
                sdks[parts[2].removeprefix("go")] = path.strip()
        return sdks

    @classmethod
    async def process(
        cls,
        host: Host,
        search_dirs: Sequence[str] = DEFAULT_SEARCH_DIRS,
    ) -> dict[str, str]:
        state = getattr(host, "go_sdks", None)
        if state is not None:
            # SimulatedHost seam: the version -> path store. The store models
            # the whole host, not one scan, so ``search_dirs`` cannot narrow
            # it -- simulated state is expected to hold what the default (or
            # requested) directories would yield.
            return dict(state)
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.command(search_dirs))
        return cls.parse(stdout.splitlines())


class GoModInfo(TypedDict):
    """
    Attributes:
        module: The module path declared by the ``module`` directive
            (``"example.com/app"``).
        go: The ``go`` directive's language version (``"1.23"``), ``""`` when
            the file carries no directive.
        require: Direct dependency import path -> required version
            (``{"github.com/gin-gonic/gin": "v1.10.0"}``); ``go mod edit``
            marks indirect requirements and they are excluded, so the mapping
            is empty when the module has no direct requires.
    """

    # The answer shape of the ``GoModuleInfo`` fact. A TypedDict whose
    # ``require`` is an ordinary mapping -- the proposal's shape, with the
    # module path and go directive lifted out of the JSON wrapper names
    # (``Module.Path`` -> ``module``) because the fact's answer is the
    # proposal's dict, not go's wire format.

    module: str
    go: str
    require: dict[str, str]


class GoModuleInfo:
    """The go.mod metadata of a module directory on the host.

    **Commands:**

    ```bash
    command -v go >/dev/null 2>&1 || exit 0; cd '<path>' 2>/dev/null && go mod edit -json 2>/dev/null
    ```

    **Returns:**

    ```python
    GoModInfo | None
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/go/go_go_module_info.py``.
    """

    # Adapted from the proposed ``GoModuleInfo`` design, which mixed an awk
    # scan of go.mod with (inconsistently) ``go env -json`` -- environment,
    # not module metadata. This port asks go itself for the authoritative
    # machine view of the file: ``go mod edit -json`` parses module, go and
    # require directives -- including block-form ``require ( ... )`` and the
    # ``// indirect`` markers no awk excerpt reproduced faithfully -- and
    # performs no network access. A directory without a parsable go.mod makes
    # the command exit non-zero with hidden stderr, and every failure mode
    # (missing directory, missing file, invalid syntax, missing toolchain)
    # reaches the same empty output.
    #
    # The proposal reported absence as ``{}``; like ``GoVersion`` this
    # single-valued fact answers ``None`` (the ``Which`` precedent). Only
    # direct requirements are mapped, the proposal's stated intent; indirect
    # ones are visible in go's own JSON and deliberately dropped here.

    @classmethod
    def command(cls, path: str) -> str:
        """Shell reading the module metadata of one directory.

        The path is ``shlex``-quoted and both the ``cd`` and the go command
        hide their stderr, so a missing directory is an empty answer rather
        than a message on the wire.
        """
        return (
            "command -v go >/dev/null 2>&1 || exit 0; "
            f"cd {shlex.quote(path)} 2>/dev/null "
            "&& go mod edit -json 2>/dev/null"
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> GoModInfo | None:
        """Parse ``go mod edit -json`` into the module/go/require triple.

        Output that is not JSON, not an object, or carries no module path is
        no answer at all (``None``) -- the proposal's ``"module" in info``
        rule. The go directive defaults to ``""`` when the file has none, and
        ``require`` maps only entries that carry a path and are not marked
        ``Indirect``. Pure and testable.
        """
        try:
            data = json.loads("\n".join(output))
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        module = data.get("Module")
        if not isinstance(module, dict) or not module.get("Path"):
            return None
        require = {
            entry["Path"]: entry.get("Version", "")
            for entry in data.get("Require") or []
            if isinstance(entry, dict)
            and entry.get("Path")
            and not entry.get("Indirect")
        }
        return {"module": module["Path"], "go": data.get("Go", ""), "require": require}

    @classmethod
    async def process(cls, host: Host, path: str) -> GoModInfo | None:
        modules = getattr(host, "go_modules", None)
        if modules is not None:
            # SimulatedHost seam: module directory -> its GoModInfo, keyed by
            # the same path the caller asked for. Freshly built dicts (and a
            # fresh require mapping) so callers never mutate simulated state;
            # an unseeded directory reads None like a missing go.mod.
            info = modules.get(path)
            if info is None:
                return None
            return {**info, "require": dict(info.get("require", {}))}
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.command(path))
        return cls.parse(stdout.splitlines())


class GoBuildInfo(TypedDict):
    """
    Attributes:
        go_version: The compiler that built the binary, verbatim from the
            header line (``"go1.23.4"`` -- the ``go`` prefix is kept here,
            unlike the ``GoVersion`` strip).
        path: The main package's import path from the ``path`` row, ``""``
            when the binary carries none.
        module_path: The ``mod`` row's module path, ``""`` for a binary
            built without module info.
        module_version: The ``mod`` row's version -- ``(devel)`` for a
            ``go build`` artifact, ``""`` when there is no ``mod`` row.
        settings: ``build`` rows as setting -> value (``"buildmode"",
            ``"GOOS"``, ``"CGO_ENABLED"``, ``"vcs.revision"`` ...), the
            leading ``-`` stripped from flag-style names.
    """

    # The answer shape of the ``GoBinaryBuildInfo`` fact. The proposal folded
    # the whole ``mod`` row into one joined string; this port splits it into
    # ``module_path``/``module_version`` -- the ``GoPackageInfo`` vocabulary
    # the module already uses -- and does not model the row's checksum half.

    go_version: str
    path: str
    module_path: str
    module_version: str
    settings: dict[str, str]


class GoBinaryBuildInfo:
    """The build metadata embedded in a compiled Go binary.

    **Commands:**

    ```bash
    command -v go >/dev/null 2>&1 || exit 0; go version -m '<path>' 2>/dev/null
    ```

    **Returns:**

    ```python
    GoBuildInfo | None
    ```

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in
        ``examples/modules/go/go_go_binary_build_info.py``.
    """

    # Adapted from the proposed ``GoBinaryBuildInfo`` design: the same
    # ``go version -m`` reader, the same self-guarded absence reading as the
    # sibling go facts (missing toolchain, missing file and a non-Go binary
    # all hide their stderr behind the guard/redirect and read None -- the
    # proposal's ``{}``). The first non-blank, non-tab line is the per-binary
    # ``<file>: go1.x`` header every ``go version -m`` print starts with
    # (the row grouping ``GoPackages.parse`` uses); tab-indented rows then
    # fill the fields: ``path`` and ``mod`` verbatim, ``build`` rows into
    # ``settings`` with a flag name's leading ``-`` stripped.
    #
    # Unlike the ``GoPackages`` install listing, a ``(devel)`` module version
    # is KEPT (a ``go build`` artifact still reports its provenance -- that
    # is the question this fact answers), and ``dep``/``buildInformation``
    # rows match no key and contribute nothing, exactly like the sibling
    # reader's rule. ``build`` rows come in two printed forms (verified
    # against real ``go version -m`` output): ``build<TAB>-flag=value`` for
    # compiler flags and environment (the ``=`` half belongs to the name),
    # and ``build<TAB>vcs.revision<TAB>abc123`` for the long VCS fields --
    # both are split into the same setting -> value mapping, the leading
    # ``-`` stripped from flag names.

    @classmethod
    def command(cls, path: str) -> str:
        """Shell reading one binary's embedded build info.

        The path is ``shlex``-quoted and go's own errors (unreadable file,
        non-Go binary) stay on the hidden stderr, so every failure mode
        reaches the same empty output.
        """
        return (
            "command -v go >/dev/null 2>&1 || exit 0; "
            f"go version -m {shlex.quote(path)} 2>/dev/null"
        )

    @classmethod
    def parse(cls, output: Iterable[str]) -> GoBuildInfo | None:
        """Parse the header plus ``path``/``mod``/``build`` rows.

        No non-blank line, or a first line that is already a tab-indented
        row (not build-info output at all), reads as ``None``. The header's
        last field is the compiler version when it is ``go``-prefixed (the
        file half may carry spaces, so the position is not counted from).
        A ``build`` row carries its value after the name with ``=``
        (``-buildmode=exe``) or as a further tab column (``vcs.revision``);
        either way the row maps to one setting. Missing rows leave their
        fields empty; entries are returned in the order go printed them.
        Pure and testable.
        """
        lines = list(output)
        header_index: int | None = None
        for index, line in enumerate(lines):
            if line.strip():
                header_index = index
                break
        if header_index is None or lines[header_index].startswith("\t"):
            return None
        tokens = lines[header_index].split()
        go_version = tokens[-1] if tokens[-1].startswith("go") else ""
        path = ""
        module_path = ""
        module_version = ""
        settings: dict[str, str] = {}
        for line in lines[header_index + 1:]:
            if not line.startswith("\t"):
                continue
            row = line.split("\t")
            if len(row) < 3:
                continue
            kind, name = row[1], row[2]
            if kind == "path":
                path = name
            elif kind == "mod":
                module_path = name
                module_version = row[3] if len(row) >= 4 else ""
            elif kind == "build":
                # Two printed forms: ``-flag=value`` in one column, or the
                # name and a separate tab column for the long vcs fields.
                if len(row) >= 4:
                    settings[name.removeprefix("-")] = row[3]
                elif "=" in name:
                    setting, _, value = name.partition("=")
                    settings[setting.removeprefix("-")] = value
        return {
            "go_version": go_version,
            "path": path,
            "module_path": module_path,
            "module_version": module_version,
            "settings": settings,
        }

    @classmethod
    async def process(cls, host: Host, path: str) -> GoBuildInfo | None:
        binaries = getattr(host, "go_binaries", None)
        if binaries is not None:
            # SimulatedHost seam: binary path -> its GoBuildInfo, keyed by the
            # same path the caller asked for. Freshly built dicts (and a fresh
            # settings mapping) so callers never mutate simulated state; an
            # unseeded path reads None like an unreadable binary.
            info = binaries.get(path)
            if info is None:
                return None
            return {**info, "settings": dict(info.get("settings", {}))}
        # Real transport: gather over asyncssh from the host's connection.
        stdout = await host.run_shell(cls.command(path))
        return cls.parse(stdout.splitlines())


# --- Operations --------------------------------------------------------------

#: The go bin directory resolver both the fact and the removal command run
#: first: ``GOBIN`` when go reports one, else ``$GOPATH/bin``.
_GO_BINDIR_GUARD = (
    'BINDIR=$(go env GOBIN); [ -z "$BINDIR" ] && '
    'BINDIR="$(go env GOPATH)/bin"; '
)


@dataclass(frozen=True)
class GoInstallCommand(Command):
    """``go install <pkg>@<version> ...``.

    The install *and* the ``latest`` re-install direction of ``go.packages``
    -- go has no ``update`` verb, so pyinfra builds the same ``go install``
    command twice, and so does the operation: one descriptor per pending
    batch. ``packages`` holds go's own tokens -- ``<path>@<version>`` pins and
    ``<path>@latest`` alike, translated from the request's
    ``<path>=<version>`` spelling by ``_go_package_spec`` -- and every token
    is ``shlex``-quoted, so a metacharacter can never reach the remote shell
    unescaped.
    """

    packages: tuple[str, ...]

    def shell(self) -> str:
        parts = ["go", "install"]
        parts.extend(shlex.quote(package) for package in self.packages)
        return " ".join(parts)


@dataclass(frozen=True)
class GoRemoveCommand(Command):
    """Resolve the go bin directory, then ``rm -f "$BINDIR"/<binary> ...``.

    The ``present=False`` direction of ``go.packages`` (pyinfra's removal
    command). go keeps no install database -- the binary *is* the
    installation -- so removal resolves the bin directory exactly as the
    ``GoPackages`` fact does and deletes the files. ``packages`` holds the
    import paths; each renders as ``_binary_name`` spells the executable go
    linked it as (the last path segment, minus a Semantic-Import-Versioning
    ``/vN`` suffix). The guard prefix and the ``"$BINDIR"`` quoting are
    pyinfra's verbatim; each binary name is ``shlex``-quoted on top.
    """

    packages: tuple[str, ...]

    def shell(self) -> str:
        command = f"{_GO_BINDIR_GUARD}rm -f"
        if self.packages:
            targets = " ".join(
                f'"$BINDIR"/{shlex.quote(_binary_name(package))}'
                for package in self.packages
            )
            command = f"{command} {targets}"
        return command


async def packages(
    ctx: OpContext,
    packages: str | list[str] | tuple[str, ...] | None = None,
    present: bool = True,
    latest: bool = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Install or remove Go binaries installed via ``go install``.

    Args:
        ctx (OpContext): Execution context for the operation.
        packages (str | list[str] | tuple[str, ...] | None, optional): Go
            module binaries to ensure, each spelled as its full import path
            (``"honnef.co/go/tools/cmd/staticcheck"``). Prefer tuples when
            building requests via ``op()``, since ``OperationRequest`` kwargs
            must stay hashable. Versions can be pinned as
            ``<pkg>=<version>``, where the special ``=latest`` asks for the
            newest module version -- the same thing an unpinned path asks
            for. Defaults to None.
        present (bool, optional): Whether the packages should be installed.
            Defaults to True.
        latest (bool, optional): Whether to re-install installed packages
            without a pinned version. Not idempotent: without a registry to
            compare against, an installed unversioned package re-runs
            ``go install`` and reports changed on every run. Defaults to
            False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: Generated go package commands.

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        ```python
        from reemote import go, op

        # Install the newest version of a module binary. go writes the
        # connecting user's go bin directory, so no escalation.
        # -> go install honnef.co/go/tools/cmd/staticcheck@latest
        yield op(
            go.packages,
            name="Install staticcheck",
            packages=("honnef.co/go/tools/cmd/staticcheck",),
        )

        # A `<pkg>=<version>` pin is translated to go's own `<pkg>@<version>`
        # token -> go install github.com/bitmagnet-io/bitmagnet@v0.10.0
        yield op(
            go.packages,
            name="Install a pinned binary",
            packages=("github.com/bitmagnet-io/bitmagnet=v0.10.0",),
        )

        # Remove a binary (present=False): the command deletes the
        # executable from the go bin directory
        # -> ... rm -f "$BINDIR"/staticcheck
        yield op(
            go.packages,
            name="Remove staticcheck",
            packages=("honnef.co/go/tools/cmd/staticcheck",),
            present=False,
        )

        # Re-install installed, unpinned binaries with the newest version
        # (this direction is not idempotent) ->
        # go install golang.org/x/tools/cmd/stringer@latest
        yield op(
            go.packages,
            name="Update stringer",
            packages=("golang.org/x/tools/cmd/stringer",),
            latest=True,
        )
        ```

        Examples can be found in ``examples/modules/go/go_packages.py``.
    """

    # pyinfra ``go.packages`` parity.
    #
    # The ``GoPackages`` fact supplies the installed versions, so an unpinned
    # request is present when the import path has *any* installed version
    # (there is no requested version to compare -- pyinfra installs
    # ``<pkg>@latest``) and a pinned ``<pkg>=<version>`` is present only when
    # that *exact* version is installed (the ``ensure_packages`` rule
    # ``apt.packages``, ``npm.packages`` and ``gem.packages`` follow). The
    # user-facing pin is reemote's shared ``<pkg>=<version>`` spelling,
    # translated to go's own ``<pkg>@<version>`` token by
    # ``_go_package_spec`` -- the npm precedent -- so pyinfra's ``@`` input
    # spelling is not accepted; its special ``@latest`` survives as the
    # ``=latest`` pin, treated exactly like no version at all in both the
    # comparison and the token.
    #
    # ``latest`` keeps pyinfra's documented non-idempotence (unlike apt, go
    # has no pending-upgrade view to gate on): every installed, unversioned
    # request re-yields ``go install <pkg>@latest`` and reports changed, the
    # npm/gem legacy-``latest`` behavior. The install batch and the upgrade
    # batch are pyinfra's two separate commands of the same form -- emitted
    # as two ``GoInstallCommand``s, install first -- because go has no
    # ``update`` verb.
    #
    # Removal is name-based, exactly pyinfra's and deliberately unlike the
    # version-aware apt/gem removal: a binary carries no per-version state to
    # match, so a path with any installed version is removed whatever pin
    # rode with it, and a path requested twice contributes one ``rm -f``
    # token in request order -- the request sequence must stay deterministic.
    #
    # Nothing here needs root: ``go install`` writes the *connecting user's*
    # go bin directory and the fact reads that same directory, so an
    # escalated request would install into root's tree instead -- leave the
    # request unprivileged. ``**kwargs`` absorbs pyinfra's global arguments
    # (``op()`` already strips ``name``/``sudo``/``su``/``doas``).
    reasons: list[str] = []

    # Normalize: accept a bare string, a list, or None (pyinfra's own
    # shorthand and its no-op).
    if packages is None:
        requested: tuple[str, ...] = ()
    elif isinstance(packages, str):
        requested = (packages,)
    else:
        requested = tuple(packages)

    if requested:
        installed = await ctx.facts.get(GoPackages)
        # The fact reports one entry per installed path/version pair; group
        # the versions by path so the lookups below read as the version-list
        # comparison ``ensure_packages`` does.
        versions_by_path: dict[str, list[str]] = {}
        for entry in installed:
            versions_by_path.setdefault(entry["path"], []).append(entry["version"])

        def is_installed(spec: str) -> bool:
            name, version = _split_version(spec)
            versions = versions_by_path.get(name)
            if not versions:
                return False
            # An unpinned request -- and go's special ``latest`` pin -- has
            # no version to compare: any installed version satisfies it.
            return version is None or version == "latest" or version in versions

        if present:
            to_install = tuple(pkg for pkg in requested if not is_installed(pkg))
            # ``latest``: installed, unversioned requests are re-install
            # candidates; a pinned request never is.
            to_upgrade = (
                tuple(
                    pkg
                    for pkg in requested
                    if _split_version(pkg)[1] in (None, "latest")
                    and is_installed(pkg)
                )
                if latest
                else ()
            )
            if to_install:
                ctx.changed = True
                reasons.append(f"{len(to_install)} package(s) to install")
                yield GoInstallCommand(
                    packages=tuple(_go_package_spec(pkg) for pkg in to_install)
                )
            if to_upgrade:
                ctx.changed = True
                reasons.append(f"{len(to_upgrade)} package(s) to upgrade")
                yield GoInstallCommand(
                    packages=tuple(_go_package_spec(pkg) for pkg in to_upgrade)
                )
            if not to_install and not to_upgrade:
                reasons.append("all packages present")
        else:
            to_remove: list[str] = []
            for package in requested:
                name = _split_version(package)[0]
                if versions_by_path.get(name) and name not in to_remove:
                    to_remove.append(name)
            if to_remove:
                ctx.changed = True
                reasons.append(f"{len(to_remove)} package(s) to remove")
                yield GoRemoveCommand(packages=tuple(to_remove))
            else:
                reasons.append("no packages to remove")
    else:
        reasons.append("no packages requested")

    ctx.reason = "; ".join(reasons)


# --- go.env ------------------------------------------------------------------


@dataclass(frozen=True)
class GoEnvSetCommand(Command):
    """One ``go env -w KEY=VAL ...`` batch of persistent go config settings.

    The write direction of ``go.env``. Each ``KEY=VAL`` is rendered as one
    ``shlex``-quoted token so a value carrying spaces or metacharacters
    reaches go as a single argument, and the pairs are emitted in sorted key
    order so the command text is stable across the prepare and apply phases
    whatever order the request spelled them in.
    """

    #: ``(key, value)`` pairs to persist; rendered sorted for a stable shell.
    assignments: tuple[tuple[str, str], ...] = ()

    def shell(self) -> str:
        tokens = " ".join(
            shlex.quote(f"{key}={value}") for key, value in sorted(self.assignments)
        )
        return f"go env -w {tokens}"


@dataclass(frozen=True)
class GoEnvUnsetCommand(Command):
    """One ``go env -u KEY ...`` batch removing persistent go config keys.

    The unset direction of ``go.env`` (a ``None`` value in the request). Keys
    render in sorted order, each ``shlex``-quoted.
    """

    #: Keys to remove from the go env file; rendered sorted.
    keys: tuple[str, ...] = ()

    def shell(self) -> str:
        return "go env -u " + " ".join(shlex.quote(key) for key in sorted(self.keys))


async def env(
    ctx: OpContext,
    settings: tuple[tuple[str, str | None], ...],
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Set or unset persistent go env settings via ``go env -w`` / ``go env -u``.

    Args:
        ctx (OpContext): Execution context for the operation.
        settings (tuple[tuple[str, str | None], ...]): ``(key, value)`` pairs
            to persist; a ``None`` value unsets that key. Spelled as a tuple
            of pairs rather than a dict because ``op()`` rejects unhashable
            arguments.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The unset command (when keys are removed) then the set
            command (when values change), each only for the keys that drift;
            nothing when every key already holds its desired value.

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/go/go_env.py``.
    """

    # The design's ``go_env`` operation, ported (there is no upstream pyinfra
    # go env operation, so the option set is the design's own). The mapping is
    # a tuple-of-pairs for hashability -- the ``sysctl`` exemplar. Idempotence
    # is per key against ``GoEnv``: an unset fires only for a key that
    # currently holds a non-empty value, a set only for a key whose value
    # differs, so an already-converged request yields no command at all. The
    # unset batch precedes the set batch so a rename (unset old, set new)
    # reads cleanly. ``go env -w``/``-u`` edit the *connecting user's* go env
    # file, so the request stays unprivileged -- flagging ``sudo`` would edit
    # root's file instead. ``**kwargs`` absorbs pyinfra's global arguments
    # (``op()`` already strips ``name``/``sudo``/``su``/``doas``).
    reasons: list[str] = []
    desired = dict(settings)
    if not desired:
        ctx.reason = "no settings requested"
        return

    current = await ctx.facts.get(GoEnv, tuple(sorted(desired)))

    to_unset = tuple(
        key
        for key, value in desired.items()
        if value is None and current.get(key, "") != ""
    )
    to_set = tuple(
        (key, value)
        for key, value in desired.items()
        if value is not None and current.get(key) != value
    )

    if to_unset:
        ctx.changed = True
        reasons.append(f"unset {', '.join(sorted(to_unset))}")
        yield GoEnvUnsetCommand(keys=to_unset)
    if to_set:
        ctx.changed = True
        reasons.append(f"set {', '.join(sorted(key for key, _ in to_set))}")
        yield GoEnvSetCommand(assignments=to_set)
    if not to_unset and not to_set:
        reasons.append("settings already in place")

    ctx.reason = "; ".join(reasons)


# --- go.cache_clean ----------------------------------------------------------


@dataclass(frozen=True)
class GoCleanCommand(Command):
    """``go clean [-cache] [-modcache]`` -- clear the build and/or module cache.

    Both flags of the ``go.cache_clean`` operation fold into one command: a
    build-cache-only request renders ``go clean -cache`` and selecting the
    module cache appends ``-modcache``. The operation never emits this with
    neither flag set.
    """

    #: Clear the build cache (``-cache``). Defaults to True.
    build_cache: bool = True
    #: Clear the module cache (``-modcache``). Defaults to False.
    mod_cache: bool = False

    def shell(self) -> str:
        flags = []
        if self.build_cache:
            flags.append("-cache")
        if self.mod_cache:
            flags.append("-modcache")
        return "go clean " + " ".join(flags)


async def cache_clean(
    ctx: OpContext,
    clear_build_cache: bool = True,
    clear_mod_cache: bool = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Clean the Go build and/or module caches via ``go clean``.

    Args:
        ctx (OpContext): Execution context for the operation.
        clear_build_cache (bool, optional): Clear the build cache
            (``go clean -cache``). Defaults to True.
        clear_mod_cache (bool, optional): Clear the module cache
            (``go clean -modcache``). Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The clean command, always when a cache is selected (the
            operation is stateless); nothing only when neither is.

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/go/go_cache_clean.py``.
    """

    # The design's ``go_cache_clean`` operation, ported *without* its
    # ``min_total_mb`` size gate. That gate read a ``GoCacheStats`` fact that
    # does not exist in reemote, and rather than add a fact for a threshold no
    # caller asked for, the operation is stateless -- it always cleans when
    # asked, exactly like ``dnf.clean``/``dnf.makecache``, leaving the caller
    # to gate it in the deploy. ``go clean`` empties the *connecting user's*
    # ``$GOCACHE``/``$GOMODCACHE``, so the request stays unprivileged; a
    # ``sudo`` clean would empty root's caches instead. Selecting neither
    # cache is a no-op (there is nothing to clear). ``**kwargs`` absorbs
    # pyinfra's global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``/``doas``).
    reasons: list[str] = []
    if not clear_build_cache and not clear_mod_cache:
        ctx.reason = "no caches selected"
        return

    ctx.changed = True
    selected = []
    if clear_build_cache:
        selected.append("build cache")
    if clear_mod_cache:
        selected.append("module cache")
    reasons.append(f"clean {', '.join(selected)}")
    yield GoCleanCommand(build_cache=clear_build_cache, mod_cache=clear_mod_cache)

    ctx.reason = "; ".join(reasons)


# --- go.build ----------------------------------------------------------------


@dataclass(frozen=True)
class GoBuildCommand(Command):
    """Build a Go package into an output binary with ``go build``.

    The single mutation of ``go.build``. Renders the whole step as a
    ``sh -c``-wrapped compound -- ``mkdir -p`` of the output's parent, ``cd``
    into the source directory, then ``go build`` -- for two reasons: the
    chain has to run together (the ``cd`` gives ``go build`` the module
    context, the ``mkdir`` gives it a place to write), and the ``sh -c``
    wrapper lets a request ``env=`` prefix (``GOOS``/``GOARCH``/
    ``CGO_ENABLED``) apply to the entire build rather than only its leading
    ``mkdir``. ``tags`` joins into one comma list ``-tags`` argument and
    ``ldflags`` is one ``-ldflags`` argument; every path and value is
    ``shlex``-quoted both within the inner chain and once around it.
    """

    #: Module directory to build from (the inner ``cd``).
    src_dir: str
    #: Binary path to write (``go build -o``); its parent is mkdir'd.
    output_path: str
    #: Package to build, relative to ``src_dir`` or a module path.
    pkg: str = "."
    #: A single ``-ldflags`` argument string, or None to omit.
    ldflags: str | None = None
    #: Build tags joined into one comma-separated ``-tags`` argument.
    tags: tuple[str, ...] = ()

    def shell(self) -> str:
        parts = ["go", "build"]
        if self.tags:
            parts += ["-tags", shlex.quote(",".join(self.tags))]
        if self.ldflags:
            parts += ["-ldflags", shlex.quote(self.ldflags)]
        parts += ["-o", shlex.quote(self.output_path), shlex.quote(self.pkg)]
        if "/" in self.output_path:
            out_dir = self.output_path.rsplit("/", 1)[0] or "/"
        else:
            # A bare name builds into the current directory -- ``mkdir -p``
            # of the name itself would collide with the binary it writes.
            out_dir = "."
        inner = (
            f"mkdir -p {shlex.quote(out_dir)} && "
            f"cd {shlex.quote(self.src_dir)} && {' '.join(parts)}"
        )
        return f"sh -c {shlex.quote(inner)}"


async def build(
    ctx: OpContext,
    src_dir: str,
    output_path: str,
    pkg: str = ".",
    ldflags: str | None = None,
    tags: Sequence[str] | None = None,
    force: bool = False,
    **kwargs: Any,
) -> AsyncGenerator[Command]:
    """Build a Go package into ``output_path`` when it is missing, stale or forced.

    Args:
        ctx (OpContext): Execution context for the operation.
        src_dir (str): The module directory to build from.
        output_path (str): The binary path to write with ``go build -o``.
        pkg (str, optional): The package to build, relative to ``src_dir`` or
            a module path. Defaults to ".".
        ldflags (str | None, optional): A single ``-ldflags`` argument string
            (``"-X main.version=1.2.3"``). Defaults to None.
        tags (Sequence[str] | None, optional): Build tags joined into one
            ``-tags`` list. Prefer a tuple when building requests via ``op()``,
            which rejects unhashable arguments. Defaults to None.
        force (bool, optional): Build even when a matching binary is already
            present. Defaults to False.
        **kwargs: Additional keyword arguments.

    Yields:
        Command: The build command, only when the output binary is missing,
            was built by a different toolchain, or ``force`` is set.

    **Raises:**

    ```python
    HostConnectionError
    ```

    Examples:
        Examples can be found in ``examples/modules/go/go_build.py``.
    """

    # The design's ``go_build`` operation, ported with two deliberate
    # deviations. (1) It takes NO ``env`` parameter: build-time
    # GOOS/GOARCH/CGO_ENABLED come through reemote's ``op(env=...)``
    # meta-parameter (issue #66, which rejected a per-operation env keyword
    # because the driver already owns env rendering), and ``GoBuildCommand``
    # wraps its compound in ``sh -c`` so that ``env K=V ...`` prefix reaches
    # ``go build`` and not just the leading ``mkdir``. (2) Idempotence
    # compares only the embedded compiler ``go_version`` against the current
    # toolchain, NOT the design's GOOS/GOARCH settings -- ``go version -m``
    # does not record the target platform unless the build injected it, so
    # that branch could never fire, and a caller switching target platforms
    # passes ``force``. A missing binary, a toolchain mismatch or ``force``
    # each rebuild; an up-to-date one is a no-op. ``go build`` writes
    # ``output_path`` as the connecting user, so the request stays
    # unprivileged unless that path is root-owned. ``**kwargs`` absorbs
    # pyinfra's global arguments (``op()`` already strips
    # ``name``/``sudo``/``su``/``doas``).
    reasons: list[str] = []
    normalized_tags = tuple(tags) if tags else ()
    descriptor_kwargs = {
        "src_dir": src_dir,
        "output_path": output_path,
        "pkg": pkg,
        "ldflags": ldflags,
        "tags": normalized_tags,
    }

    if force:
        ctx.changed = True
        reasons.append(f"forced build of {output_path}")
        yield GoBuildCommand(**descriptor_kwargs)
        ctx.reason = "; ".join(reasons)
        return

    current = await ctx.facts.get(GoVersion)
    current_version = current["version"] if current else None
    existing = await ctx.facts.get(GoBinaryBuildInfo, output_path)

    if existing is not None and (
        not current_version or existing["go_version"] == f"go{current_version}"
    ):
        reasons.append(f"{output_path} already built with the current toolchain")
    else:
        ctx.changed = True
        if existing is None:
            reasons.append(f"{output_path} missing, will build")
        else:
            reasons.append(
                f"{output_path} built by "
                f"{existing['go_version'] or 'an unknown toolchain'}, will rebuild"
            )
        yield GoBuildCommand(**descriptor_kwargs)

    ctx.reason = "; ".join(reasons)


__all__ = [
    "GoBinaryBuildInfo",
    "GoBuildCommand",
    "GoBuildInfo",
    "GoCleanCommand",
    "GoEnv",
    "GoEnvSetCommand",
    "GoEnvUnsetCommand",
    "GoInstallCommand",
    "GoInstalledSDKs",
    "GoModInfo",
    "GoModuleInfo",
    "GoPackageInfo",
    "GoPackages",
    "GoRemoveCommand",
    "GoVersion",
    "GoVersionInfo",
    "build",
    "cache_clean",
    "env",
    "packages",
]
