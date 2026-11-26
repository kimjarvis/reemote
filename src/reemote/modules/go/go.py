# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# The ``go`` module: facts in one source.
#
# Fact side: ``GoPackages`` reports the Go binaries installed via
# ``go install`` as ``GoPackageInfo`` entries (the binary's main-package
# import path plus one module version), parsed from the ``go version -m``
# rows of the executable listing of the go bin directory. Like every fact it
# is phase-unaware: its ``process`` coroutine just gathers the raw value.

from collections.abc import Iterable
from typing import TypedDict

from ...hosts import Host

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


__all__ = [
    "GoPackageInfo",
    "GoPackages",
]
