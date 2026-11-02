# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: ensure a sysctl setting (pyinfra server.sysctl parity).

This is a *usage* example, not part of the library. It is reemote5's answer
to pyinfra's ``server.sysctl`` operation: no bespoke operation is ported,
because the persist half is a file edit fully expressible with the generic
``files.edit`` content-transform primitive plus the elevated ``files.move``
-- the same spool-and-install recipe as ``server_op_locale.py`` -- and the
runtime half is a single ``sysctl key=value`` shell line this example
defines itself over the public raw ``StringCommand`` descriptor.

pyinfra's ``server.sysctl(key, value, persist=False,
persist_file="/etc/sysctl.conf")`` normalizes ``value`` (a list joins with
spaces for the shell; digits become ints so the fact comparison matches),
reads the ``Sysctl`` fact for the key, runs ``sysctl 'key'='value'`` only
when the live value differs, and -- independently of that comparison --
ensures the line ``key = value`` in the persist file through ``files.line``
(match ``key[[:space:]]*=[[:space:]]*value``, replace with ``key = value``,
append when absent). The recipe mirrors all of that:

1. Two fact SNAPSHOTS: ``gather_fact`` reads ``Sysctl`` for the one key and
   ``FileContents`` of ``/etc/sysctl.conf`` from every host (read-only
   SSH). The pure ``sysctl_persist_edit`` function performs pyinfra's
   ``files.line`` lookup with a Python regex over the content -- reemote5
   ships no grep-style ``FindInFile`` fact, and ``FileContents`` plus local
   parsing is its content-read convention.
2. Two independent skip gates on the frozen snapshots, matching pyinfra's
   two independent decisions: the runtime gate (``sysctl_needed``) is
   pyinfra's ``existing_value != value`` fact comparison on the normalized
   value; the persist gate compares the transformed content against the
   file's current text (pyinfra's ``files.line`` no-ops when the exact
   ``key = value`` line is already present). A host can need either half,
   both or neither.
3. Run 1 applies the runtime value: the demo-side ``sysctl_set`` operation
   yields ``sysctl key=value`` (each side shell-quoted, pyinfra's
   ``QuoteString`` parity), flagged ``sudo=True``. reemote5 ships no
   generic "run a shell command" operation, so this example defines the
   stateless one-liner itself; the snapshot gate upstream decides whether
   it runs at all.
4. Run 2 spools: ``files.edit`` writes the desired sysctl.conf content to
   ``/tmp/sysctl.conf`` (login-user-writable). Each host's transform is
   built ONCE by the ``make_transform`` factory closing over the frozen
   desired content -- a stable callable across both phases, the
   determinism-safe split shown in ``examples/general/two_deploy_split.py``.
5. Run 3 installs: ``files.move`` moves the spool file into ``/etc`` with
   ``overwrite=True`` and ``sudo=True``. The edit and the move cannot share
   one deploy: ``files.move`` validates ``src`` in the prepare phase too,
   and prepare never sends, so the spool file must really exist first.

Deliberate deviations from pyinfra, decided at migration time:

* The persist edit (``files.line``'s sed/echo) is replaced by the
  ``files.edit`` transform: reemote5 ports no per-format file editors, and
  its transfers are unprivileged, so root-owned ``/etc/sysctl.conf`` is
  written through the spool-and-install vehicle instead of an elevated
  sed/echo. pyinfra's timestamped sed backup dance goes with it.
* The line regex escapes the key and the value literally (``re.escape``);
  pyinfra interpolates them raw into a grep pattern, where the ``.`` in
  ``fs.file-max`` would match any character. The POSIX ``[[:space:]]*``
  class is translated verbatim (minus the newline -- matching is per line).
* pyinfra's ``files.line`` search semantics are kept: the pattern is
  unanchored, so a commented ``# key = value`` line matches and is
  normalized (uncommented) in place, and a longer value sharing the prefix
  (``key = 1000000``) matches too. A *stale* ``key = other`` line does NOT
  match and survives beside the new line, exactly like pyinfra -- the last
  assignment wins when sysctl.conf is replayed at boot.
* The desired content always ends in one trailing newline (the recipe's
  ``"\\n".join(lines) + "\\n"`` house style), so a file missing its final
  newline is rewritten once even when the line itself is already correct.
* The runtime ``sysctl`` step is stateless like pyinfra's yielded command;
  pyinfra's noop message ("sysctl {key} is set to {value}") becomes the
  controller-side skip, printed from the frozen snapshot.

Re-running the whole script is a no-op once the live value and the persist
line both match: the snapshot gates skip every run. Reading the settings
back without changing anything is the companion ``server_sysctl.py``
example's job.

Run it with::

    uv run python examples/modules/server/server_op_sysctl.py
"""

import asyncio
import re
import shlex
import sys
from collections.abc import AsyncGenerator, Callable, Generator
from typing import Any

from reemote5 import (
    Command,
    CommandError,
    FileContents,
    Host,
    HostConnectionError,
    OpContext,
    OperationRequest,
    PrivilegeError,
    StringCommand,
    Sysctl,
    files,
    format_log,
    format_plan,
    gather_fact,
    op,
    run,
)

# The setting to ensure: pyinfra's documented example (fs.file-max=100000),
# persisted so it survives a reboot. persist=False would keep run 1 alone.
SYSCTL_KEY = "fs.file-max"
SYSCTL_VALUE: str | int | list[str | int] = 100000
PERSIST = True

# pyinfra's default persist file, and the spool path: the basename
# ("sysctl.conf") is what files.move appends to /etc, landing the target.
PERSIST_FILE = "/etc/sysctl.conf"
SPOOL_PATH = "/tmp/sysctl.conf"

# Per-host spool transforms, filled ONCE in main between the snapshots and
# run 2: each is a stable callable closing over frozen desired content, so
# both phases of run 2 pass the identical object (OperationRequest compares
# the transform by value; a fresh closure per phase raises DeterminismError).
SPOOL_TRANSFORMS: dict[str, Callable[[str | None], str]] = {}

# The inventory: real hosts and their SSH connection information. No
# ``privilege`` entry: the elevated steps assume passwordless sudo for
# the login user; a password would come from ``privilege`` here, never the op.
INVENTORY = [
    {
        "host": "web1",
        "connection": {
            "address": "192.168.0.240",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
    {
        "host": "web2",
        "connection": {
            "address": "192.168.0.242",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
]


def _try_int(value: str | int) -> int | str:
    """``int()`` that returns its input unchanged on failure.

    pyinfra's ``try_int`` -- the operation side of the same coercion the
    ``Sysctl`` fact applies to its parsed values, so ``"2000"`` and ``2000``
    compare equal against the snapshot.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def normalize_value(value: str | int | list[str | int]) -> tuple[str, Any]:
    """pyinfra's ``server.sysctl`` value normalization, verbatim.

    Returns ``(string_value, normalized)``: ``string_value`` is the shell
    spelling (a list joins its items with single spaces), ``normalized`` is
    the comparison shape for the ``Sysctl`` fact -- each item ``try_int``-ed,
    a single-item list collapsing to a scalar, a whitespace-separated string
    splitting into a list, anything else a lone ``try_int``.
    """
    string_value = (
        " ".join(f"{v}" for v in value) if isinstance(value, list) else str(value)
    )
    if isinstance(value, list):
        normalized: Any = [_try_int(v) for v in value]
        if len(normalized) == 1:
            normalized = normalized[0]
    elif isinstance(value, str) and len(value.split()) > 1:
        normalized = [_try_int(v) for v in value.split()]
    else:
        normalized = _try_int(value)
    return string_value, normalized


# The shell spelling and comparison shape of the configured value, computed
# once at import: both deploy phases see the identical strings.
STRING_VALUE, NORMALIZED_VALUE = normalize_value(SYSCTL_VALUE)


def sysctl_persist_edit(content: str | None, key: str, string_value: str) -> str:
    """Pure sysctl.conf edit: ensure the ``key = value`` line.

    Mirrors the ``files.line`` call ``server.sysctl`` makes: search line
    ``key[[:space:]]*=[[:space:]]*{string_value}``, replacement
    ``key = {string_value}``. The search-style (unanchored) pattern
    matches every line containing ``key`` = ``string_value`` with optional
    surrounding spaces -- commented lines included -- and each match is
    normalized to ``key = string_value``; with no match the line is
    appended (an absent file, ``None`` content, reads as empty, pyinfra's
    echo creating it). Stale ``key = other`` lines do not match and
    survive. The key and value are escaped literally (``re.escape``), the
    documented hardening over pyinfra's raw BRE interpolation. The result
    always ends in one trailing newline and the edit is a fixpoint.
    """
    pattern = re.compile(
        rf"{re.escape(key)}[ \t\r\f\v]*=[ \t\r\f\v]*{re.escape(string_value)}"
    )
    replace = f"{key} = {string_value}"
    lines = (content or "").splitlines()
    matches = [i for i, line in enumerate(lines) if pattern.search(line)]
    if not matches:
        lines.append(replace)
    else:
        for i in matches:
            lines[i] = replace
    return "\n".join(lines) + "\n"


def sysctl_needed(sysctls: dict[str, Any], key: str, normalized: Any) -> bool:
    """pyinfra's change condition on the ``Sysctl`` fact snapshot.

    Apply only when the live value differs from the normalized desired
    value (an absent key reads as ``None`` and always differs) -- the fact
    comparison that makes pyinfra's operation idempotent, moved to the gap
    between runs. An unknown key on a real host reads as absent too: the
    ``sysctl`` command then fails loudly rather than silently no-opping.
    """
    return sysctls.get(key) != normalized


def make_transform(content: str) -> Callable[[str | None], str]:
    """Build a spool transform returning the frozen desired ``content``.

    Called once per host between the snapshots and run 2; the returned
    callable ignores the spool file's current text (the desired content was
    already computed from the real /etc/sysctl.conf) and is trivially a
    fixpoint.
    """

    def transform(current: str | None) -> str:
        return content

    return transform


async def sysctl_set(
    ctx: OpContext, key: str, string_value: str, **kwargs
) -> AsyncGenerator[Command]:
    """Apply a live sysctl value (demo-side ``sysctl key=value`` step).

    pyinfra's ``server.sysctl`` yields ``sysctl 'key'='value'`` (each side
    ``QuoteString``-ed, joined by a bare ``=``) when the fact differs;
    reemote5 ships no generic shell operation, so this example defines the
    stateless one-liner itself over the public raw ``StringCommand``
    descriptor. Like pyinfra's step it reads no fact and always reports a
    change; the ``Sysctl`` snapshot gate upstream of it decides whether it
    runs at all. The command needs root, so flag the request ``sudo=True``.
    ``**kwargs`` absorbs pyinfra's global arguments (``op()`` already
    strips ``name``/``sudo``/``su``).
    """
    ctx.changed = True
    ctx.reason = f"will set sysctl {key} to {string_value}"
    yield StringCommand(f"sysctl {shlex.quote(key)}={shlex.quote(string_value)}")


def apply(host: Host) -> Generator[OperationRequest]:
    """Run 1: apply the live value with an elevated ``sysctl key=value``."""
    yield op(
        sysctl_set,
        name="Set sysctl",
        key=SYSCTL_KEY,
        string_value=STRING_VALUE,
        sudo=True,
    )


def spool(host: Host) -> Generator[OperationRequest]:
    """Run 2: write the desired sysctl.conf content to the writable spool."""
    yield op(
        files.edit,
        name="Spool sysctl.conf",
        path=SPOOL_PATH,
        transform=SPOOL_TRANSFORMS[host.name],
    )


def install(host: Host) -> Generator[OperationRequest]:
    """Run 3: install the spool file over /etc/sysctl.conf, elevated."""
    yield op(
        files.move,
        name="Install sysctl.conf",
        src=SPOOL_PATH,
        dest="/etc",
        overwrite=True,
        sudo=True,
    )


async def main() -> None:
    try:
        # The snapshots: read every host's live value for the key and its
        # real /etc/sysctl.conf (read-only sysctl / cat).
        hosts = [Host.from_inventory(entry) for entry in INVENTORY]
        try:
            sysctls = await gather_fact(hosts, Sysctl, keys=[SYSCTL_KEY])
            contents = (
                await gather_fact(hosts, FileContents, PERSIST_FILE) if PERSIST else {}
            )
        finally:
            for host in hosts:
                await host.aclose()

        # Controller-side idempotence: pyinfra's two independent decisions
        # on the frozen snapshots. The persist gate holds even when the
        # live value already matches -- exactly like pyinfra, whose
        # files.line half runs regardless of the runtime noop.
        pending_apply = []
        pending_persist = []
        for entry in INVENTORY:
            name = entry["host"]
            if sysctl_needed(sysctls[name], SYSCTL_KEY, NORMALIZED_VALUE):
                pending_apply.append(entry)
            else:
                print(f"{name}: sysctl {SYSCTL_KEY} is set to {STRING_VALUE}")
            if not PERSIST:
                continue
            desired = sysctl_persist_edit(contents[name], SYSCTL_KEY, STRING_VALUE)
            if desired != (contents[name] or ""):
                SPOOL_TRANSFORMS[name] = make_transform(desired)
                pending_persist.append(entry)
        if not pending_apply and not pending_persist:
            print(f"All hosts already have {SYSCTL_KEY} = {STRING_VALUE}.")
            return

        # Run 1: apply the live value, elevated. pyinfra's order too --
        # the runtime command precedes the persist edit.
        if pending_apply:
            plans, _, _hosts = await run(pending_apply, apply, dry=True)
            print("\n== Run 1 plan: set sysctl (--dry) ==")
            print(format_plan(plans))

            plans, report, _hosts = await run(pending_apply, apply)
            print("\n== Run 1 log: set sysctl ==")
            assert report is not None
            print(format_log(report))

        if not pending_persist:
            return

        # Run 2: spool the transformed content to /tmp/sysctl.conf,
        # unprivileged.
        plans, _, _hosts = await run(pending_persist, spool, dry=True)
        print("\n== Run 2 plan: spool sysctl.conf (--dry) ==")
        print(format_plan(plans))

        plans, report, _hosts = await run(pending_persist, spool)
        print("\n== Run 2 log: spool sysctl.conf ==")
        assert report is not None
        print(format_log(report))

        # Run 3: the spool file now really exists, so the move's validation
        # passes in the prepare phase too. Elevated: /etc is root-owned.
        plans, _, _hosts = await run(pending_persist, install, dry=True)
        print("\n== Run 3 plan: install sysctl.conf (--dry) ==")
        print(format_plan(plans))

        plans, report, _hosts = await run(pending_persist, install)
        print("\n== Run 3 log: install sysctl.conf ==")
        assert report is not None
        print(format_log(report))
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())