# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# The ``uv`` module: facts (and later operations) in one source.
#
# Fact side: ``Packages`` reports the Python packages of the environment
# ``uv pip list`` resolves as ``PackageInfo`` entries (name plus the version
# uv reports), parsed from the JSON array ``uv pip list --format json``
# prints. Which environment that is -- an active or discovered ``.venv``, or
# the interpreter uv falls back to -- is uv's own resolution, exactly as
# pyinfra leaves it to the tool. Like every fact it is phase-unaware: its
# ``process`` coroutine just gathers the raw value.
#
# Operation side: pyinfra's ``uv.packages`` operation is not ported yet, so
# this namespace currently ships the fact alone. When the operation lands it
# reads ``Packages`` for its idempotence, the ``pip``/``npm``/``gem``
# precedent: operations are async generators that receive an ``OpContext``,
# await facts, set ``ctx.changed``/``ctx.reason``, and yield command
# descriptors; they never touch the host directly.


import json
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


__all__ = ["PackageInfo", "Packages"]
