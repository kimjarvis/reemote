# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: manage a marked block in a remote file (baseline files.block parity).

This is a *usage* example, not part of the library. It is reemote's answer to
the baseline's ``files.block`` operation: no bespoke block editor is ported, because
a managed block is fully expressible as a ``files.edit`` **content transform**.
The ``block_transform`` factory below builds that transform from the same
options the baseline's operation takes, and the generic primitive does the rest --
``FileContents`` reads the file's current text, the transform computes the
desired text, and ``files.put``'s scp transfer writes it back only when it
differs.

The factory mirrors the baseline's ``files.block`` semantics:

* ``content`` (a ``str`` split on newlines, or a list of lines) is surrounded
  by the ``marker`` lines -- ``# BEGIN REEMOTE BLOCK`` / ``# END REEMOTE BLOCK``
  by default, with ``begin``/``end`` substituting the ``{mark}``
  placeholder. The the baseline default marker text is kept verbatim so blocks
  already managed by the baseline are recognized and updated in place, not
  duplicated under a second pair of markers.
* An existing marked block is replaced in place when its content differs and
  left byte-identical when it matches (the fixpoint that makes a second edit
  a no-op).
* Without markers, ``line`` (a regex, whole-line adjusted exactly like
  the baseline's ``adjust_regex``, with ``escape_regex_characters`` to match it
  literally) positions the block ``before`` or ``after`` the first matching
  line; no match appends at the end. Without ``line``, the block is appended
  (``before == after == False``) or prepended (``before == after == True``).
* ``present=False`` removes the marked region (markers included), ignoring
  ``content`` and ``line``; a file without markers is left untouched.

Deliberate deviations from the baseline, decided at migration time:

* ``backup`` is dropped: the baseline's timestamped ``cp`` would render different
  shell text in the prepare and apply phases, tripping the determinism guard.
* ``preserve_meta`` is dropped as moot: the write is an scp overwrite, which
  keeps an existing file's inode, owner and mode anyway (a created file gets
  the login user's umask, as in the baseline).
* ``try_prevent_shell_expansion`` is dropped: the baseline already deprecates and
  ignores it, and a pure-Python transform writes ``content`` literally by
  construction -- shell metacharacters (``$(...)``, backticks, quotes) never
  reach a shell.
* Removal from an *absent* file is a true no-op: the transform passes ``None``
  through unchanged (stretching ``files.edit``'s ``str``-return contract for
  this one case), so the content-equality check reports "already up to date"
  and no empty file is created -- the baseline's "no remove required: file did not
  exist" parity.

The transform must be a **stable, hashable object**: build it ONCE (here at
module level) and pass the same callable in both phases. A factory call inside
the deploy generator would create a fresh closure per phase, diverge in the
``OperationRequest`` comparison and raise ``DeterminismError``.

The write rides the connection as the login user (a transfer, so ``sudo``/``su``
do not apply), so the target must be **writable by that user** -- here a path in
``/tmp``, which any login user can write, so the example depends on no account's
home directory existing. A root-owned file such as ``/etc/hosts`` needs a
spool-and-install write instead: ``files.install`` in one deploy (see
``files_install.py``), or a dedicated operation such as ``server.etc_hosts``
for the hosts-file case (see ``examples/modules/server/server_etc_hosts.py``).

The script runs a full lifecycle: run 1 ensures the block (twice -- the second
edit is a guaranteed no-op through the fresh per-step fact cache), run 2
removes it (twice -- the second removal likewise). Each run is shown as a
``--dry`` plan followed by the real apply log. Unlike the pytest suite -- which
injects the test framework's ``SimulatedHost`` via ``run``'s ``host_factory``
and never touches the network -- this example uses the *default* factory
(``Host.from_inventory``) and connects for real over asyncssh.

Every request carries ``when=is_centos``, the declarative eligibility
guard: the predicate reads only **static** inventory data --
``host.group``, set by the ``"group"`` key of the inventory entry -- and
is a stable module-level function, so the prepare and apply phases reach
the same skip decision. The driver skips a rejected request without
materializing it, so on hosts outside the ``"centos"`` group neither the
commands nor the facts behind them run, and plan and log render the step
as ``[skipped: when guard is_centos]`` in its lockstep slot. The path is
resolved from ``__file__``, so the example works from any working
directory; point ``INVENTORY_PATH`` at your own file to target your
machines.

The path is resolved from ``__file__``, so the example works from any
working directory; point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/files/files_block.py
"""

import asyncio
import json
import re
import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    PrivilegeError,
    files,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# The baseline's default marker constants (facts/files.py), kept verbatim so blocks
# already managed by the baseline's files.block are recognized and updated in place.
MARKER_DEFAULT = "# {mark} REEMOTE BLOCK"
MARKER_BEGIN_DEFAULT = "BEGIN"
MARKER_END_DEFAULT = "END"

# The remote file to edit: a /tmp path, writable by whatever account the
# inventory logs in as, so the unprivileged scp transfer can create it. Seed it
# with a "localhost" line to see the block positioned before that line; on an
# absent file the block simply creates it.
REMOTE_PATH = "/tmp/reemote-block.txt"

# examples/modules/files/files_block.py -> the project root, so the inventory
# is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of inventory entries -- dictionaries
    each carrying at least ``host`` and ``connection``. Returns the parsed
    list, ready to materialize into hosts; raises ``InventoryError`` (via
    ``validate_inventory``) when the document is not an array or an entry
    does not match the schema.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_centos(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"centos"``.

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision.
    """
    return host.group == "centos"


def _adjust_regex(line: str, escape_regex_characters: bool) -> str:
    """The baseline's whole-line regex adjustment (``operations/util/files.py``).

    Optionally escapes regex metacharacters so ``line`` matches literally,
    then pads the pattern with ``^.*`` / ``.*$`` so a partial-line match is
    anchored to the whole line -- exactly the regex the baseline's awk program uses.
    """
    match_line = line

    if escape_regex_characters:
        match_line = re.sub(r"([\.\\\+\*\?\[\^\]\$\(\)\{\}\-])", r"\\\1", match_line)

    if not match_line.startswith("^"):
        match_line = f"^.*{match_line}"
    if not match_line.endswith("$"):
        match_line = f"{match_line}.*$"

    return match_line


def block_transform(
    content: str | list[str] | None = None,
    present: bool = True,
    line: str | None = None,
    before: bool = False,
    after: bool = False,
    escape_regex_characters: bool = False,
    marker: str | None = None,
    begin: str | None = None,
    end: str | None = None,
) -> Callable[[str | None], str]:
    """Build the pure, idempotent ``files.edit`` transform for a marked block.

    The option set of the baseline's ``files.block``, expressed as a transform
    factory instead of a bespoke operation. Call it ONCE (module level, or a
    closure factory frozen between runs) -- the returned callable is stored in
    the ``OperationRequest`` kwargs and compared by value across phases, so a
    fresh closure per phase would raise ``DeterminismError``.

    + content: the block's lines, a ``str`` (split on newlines) or a list of
      lines. Required when ``present`` is True; ignored when removing.
    + present: True ensures the block, False removes the marked region
      (markers included).
    + line: regex positioning the block before/after the first matching line
      when no markers exist yet; no match appends at the end.
    + before: insert before the matching line (with ``line``), or prepend to
      the file (with ``after`` also True and no ``line``).
    + after: insert after the matching line (with ``line``).
    + escape_regex_characters: match ``line`` literally, escaping regex
      metacharacters first (the baseline's ``adjust_regex`` parity).
    + marker: the base marker string with a ``{mark}`` placeholder. Default
      ``# {mark} REEMOTE BLOCK`` -- the baseline's own default, kept so existing
      baseline-managed blocks are recognized.
    + begin: the ``{mark}`` value for the opening marker (default ``BEGIN``).
    + end: the ``{mark}`` value for the closing marker (default ``END``).

    The returned transform is a fixpoint: an already-correct block is returned
    byte-identical, so ``files.edit`` reports no change on a second edit. One
    documented stretch of ``files.edit``'s ``str``-return contract: removing
    from an absent file passes ``None`` through unchanged, so the
    content-equality check no-ops instead of creating an empty file (the baseline's
    "no remove required: file did not exist" parity).

    The baseline's ``backup``, ``preserve_meta`` and ``try_prevent_shell_expansion``
    options are deliberately dropped -- see the module docstring for why.
    Invalid combinations raise ``ValueError`` at factory time (the baseline's
    ``OperationValueError`` messages, raised controller-side before any run).
    """
    if present:
        if not content:
            raise ValueError("'content' must be supplied when 'present' == True")
        if line:
            if before == after:
                raise ValueError(
                    "only one of 'before' or 'after' used when 'line` is specified"
                )
        elif before != after:
            raise ValueError(
                "'line' must be supplied or 'before' and 'after' must be equal"
            )

    mark_begin = (marker or MARKER_DEFAULT).format(mark=begin or MARKER_BEGIN_DEFAULT)
    mark_end = (marker or MARKER_DEFAULT).format(mark=end or MARKER_END_DEFAULT)

    content_lines = (
        content.split("\n") if isinstance(content, str) else list(content or [])
    )
    block_lines = [mark_begin, *content_lines, mark_end]
    pattern = re.compile(_adjust_regex(line, escape_regex_characters)) if line else None

    def transform(current):
        if current is None and not present:
            # Absence-preserving no-op: None == None in files.edit's
            # content-equality check, so nothing is yielded and no empty
            # file is created (baseline parity, documented contract stretch).
            return None
        text = current or ""
        lines = text.splitlines()

        # Locate an existing marked region (begin marker, then end marker).
        start = stop = None
        if mark_begin in lines:
            i = lines.index(mark_begin)
            if mark_end in lines[i + 1 :]:
                start, stop = i, lines.index(mark_end, i + 1)

        if not present:
            if start is None:
                return text  # no markers: nothing to remove
            kept = lines[:start] + lines[stop + 1 :]
            return "\n".join(kept) + "\n" if kept else ""

        if start is not None:
            if lines[start : stop + 1] == block_lines:
                return text  # fixpoint: byte-identical, no rewrite
            new_lines = lines[:start] + block_lines + lines[stop + 1 :]
        elif pattern is not None:
            idx = next((i for i, ln in enumerate(lines) if pattern.search(ln)), None)
            if idx is None:
                new_lines = lines + block_lines  # no match: append at the end
            elif before:
                new_lines = lines[:idx] + block_lines + lines[idx:]
            else:
                new_lines = lines[: idx + 1] + block_lines + lines[idx + 1 :]
        elif before:  # before == after == True, no line: prepend
            new_lines = block_lines + lines
        else:  # before == after == False, no line: append
            new_lines = lines + block_lines
        return "\n".join(new_lines) + "\n"

    return transform


# The transforms, built ONCE at module level: the same callable objects in
# every phase of every run, which is what keeps the OperationRequest sequences
# equal (the determinism guard compares the transform by value).
ENSURE_BLOCK = block_transform(
    content="10.0.0.1 mars-one\n10.0.0.2 mars-two",
    line=".*localhost",
    before=True,
)
REMOVE_BLOCK = block_transform(present=False)


def ensure(host: Host) -> Generator[OperationRequest]:
    """Run 1: ensure the block twice -- the second edit proves the fixpoint.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases. Neither request is elevated: the write goes over the connection's
    scp channel as the login user.
    """
    yield op(
        files.edit,
        name="Ensure block",
        path=REMOTE_PATH,
        transform=ENSURE_BLOCK,
        when=is_centos,
    )
    yield op(
        files.edit, name="Re-ensure block", path=REMOTE_PATH, transform=ENSURE_BLOCK,
        when=is_centos,
    )


def remove(host: Host) -> Generator[OperationRequest]:
    """Run 2: remove the block twice -- the second removal proves the fixpoint."""
    yield op(
        files.edit,
        name="Remove block",
        path=REMOTE_PATH,
        transform=REMOVE_BLOCK,
        when=is_centos,
    )
    yield op(
        files.edit, name="Re-remove block", path=REMOTE_PATH, transform=REMOVE_BLOCK,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Run 1, --dry: prepare phase only. The file is still ``cat``'d over
        # SSH (read-only) so the plan reflects each host's real pre-deployment
        # state; no content is transferred.
        plans, _, _hosts = await run(inventory, ensure, dry=True)
        print("== Run 1 plan: ensure block (--dry) ==")
        print(format_plan(plans))

        # Run 1, real: the first edit uploads the block over scp; the second
        # sees it through the fresh per-step fact cache and reports no change.
        plans, report, _hosts = await run(inventory, ensure)
        print("\n== Run 1 log: ensure block ==")
        assert report is not None
        print(format_log(report))

        # Run 2, --dry then real: the marked region is removed (markers
        # included); the second removal is a no-op. On a file that never
        # existed the removal transform passes None through -- no empty file
        # is created.
        plans, _, _hosts = await run(inventory, remove, dry=True)
        print("\n== Run 2 plan: remove block (--dry) ==")
        print(format_plan(plans))

        plans, report, _hosts = await run(inventory, remove)
        print("\n== Run 2 log: remove block ==")
        assert report is not None
        print(format_log(report))
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON. Python's
        # own message already names the file and the reason, so print just that
        # -- no host was contacted and there is no traceback worth showing.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema. The
        # message names the offending entry by its list index, which is the
        # whole report -- and it fails before any host is materialized.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())