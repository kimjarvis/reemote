# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: enable/disable a locale (pyinfra server.locale parity).

This is a *usage* example, not part of the library. It is reemote5's answer
to pyinfra's ``server.locale`` operation: no bespoke operation is ported,
because the edit is fully expressible with the generic ``files.edit``
content-transform primitive plus the elevated ``files.move`` -- the same
spool-and-install recipe as ``files_edit_etc_hosts.py``.

**Platform prerequisite.** This is a Debian-family recipe: pyinfra's
``server.locale`` edits ``/etc/locale.gen`` and runs ``locale-gen``, both of
which exist only on Debian/Ubuntu (the ``locales`` package). RHEL-family
hosts such as the CentOS ``web1``/``web2`` demo servers have no
``/etc/locale.gen`` -- they install locales through ``glibc-langpack-*`` and
``localedef`` -- so the recipe targets the Debian-family ``ubuntu`` host,
where ``/etc/locale.gen`` ships the commented ``fr_FR.UTF-8 UTF-8``
definition line and ``locale-gen`` is on PATH. Point ``INVENTORY_PATH``
at your own file of Debian-family machines to run it elsewhere.

pyinfra's ``server.locale(locale, present=True)`` reads the ``Locales`` fact
(``locale -a``), finds the locale's definition line in ``/etc/locale.gen``
with a ``FindInFile`` grep, comments it out (``present=False``) or
uncomments it (``present=True``) through ``files.line``/``files.replace``,
and runs ``locale-gen`` after either edit. Zero or multiple matching lines
raise an error. The recipe mirrors all of that:

1. Two fact SNAPSHOTS: ``gather_fact`` reads ``FileContents`` of
   ``/etc/locale.gen`` and the installed ``Locales`` from every guarded
   host (read-only SSH). The pure ``locale_edit`` function performs pyinfra's
   line lookup with a Python regex over the content -- reemote5 ships no
   grep-style ``FindInFile`` fact, and ``FileContents`` plus local parsing
   is its content-read convention. Zero or multiple matches raise
   ``ValueError`` with pyinfra's ``OperationError`` messages, raised
   controller-side before any run.
2. The skip gate is pyinfra's own change condition, evaluated on the
   frozen ``Locales`` snapshot (``locale_needed``): enable only when the
   locale is absent from ``locale -a``, disable only when it is present.
   Hosts that already match are skipped entirely -- the script-level
   idempotence pyinfra gets from its fact comparison lives here, in the
   gap between runs.
3. Run 1 spools: ``files.edit`` writes the desired content to
   ``/tmp/locale.gen`` (login-user-writable). Each host's transform is
   built ONCE by the ``make_transform`` factory closing over the frozen
   desired content -- a stable callable across both phases, the
   determinism-safe split shown in
   ``examples/demonstrations/composition/two_deploy_split.py``.
4. Run 2 installs and regenerates: ``files.move`` moves the spool file
   into ``/etc`` with ``overwrite=True``, then the demo-side ``locale_gen``
   operation runs ``locale-gen`` -- both flagged ``sudo=True``. reemote5
   ships no generic "run a shell command" operation, so this example
   defines the stateless one-liner itself, yielding the public raw
   ``StringCommand`` descriptor. The lockstep barrier runs every host's
   move before any host regenerates.

Deliberate deviations from pyinfra, decided at migration time:

* The sed edits (``files.line``/``files.replace``) are replaced by the
  ``files.edit`` transform: reemote5 ports no per-format file editors, and
  its transfers are unprivileged, so root-owned ``/etc/locale.gen`` is
  written through the spool-and-install vehicle instead of an elevated
  sed. pyinfra's timestamped sed backup dance goes with it.
* The definition-line regex escapes the locale literally (``re.escape``);
  pyinfra interpolates it raw into a grep pattern, where ``.`` would match
  any character. The POSIX ``[[:space:]]`` class is translated verbatim.
* Uncommenting strips every ``"# "`` occurrence (pyinfra's ``str.replace``
  verbatim), so a doubly commented line is fully enabled in one edit.
  Commenting out prepends ``"# "`` and is NOT a fixpoint on its own -- a
  second disable would double the comment -- but the ``Locales`` skip gate
  never schedules it twice, exactly like pyinfra's fact comparison.
* The desired content always ends in one trailing newline (the recipe's
  ``"\\n".join(lines) + "\\n"`` house style).

Re-running the whole script is a no-op once ``locale -a`` reports the
requested state: the snapshot gate skips both runs. The ``locale-gen``
step itself is stateless (pyinfra parity) -- it always plans a command
when it runs at all.

The hosts come from the shared ``inventory.json`` in the project root,
read with ``load_inventory`` (parse + ``validate_inventory``) and resolved
from ``__file__`` so the example works from any working directory. The
snapshot gathers are guarded by ``is_ubuntu`` (``host.group == "ubuntu"``)
-- the Debian-family analogue of the ``is_centos`` guard the other
examples use, because this recipe only makes sense on Debian/Ubuntu:
``gather_fact`` takes no ``when`` parameter, so the predicate filters the
host list before the fan-out and hosts outside the ``"ubuntu"`` group are
named on a ``== Skipped (when guard is_ubuntu): ... ==`` line and never
connected. Every deploy request also carries ``when=is_ubuntu``, and only
hosts with a snapshot entry can become pending.

Run it with::

    uv run python examples/modules/server/server_op_locale.py
"""

import asyncio
import json
import re
import sys
from collections.abc import AsyncGenerator, Callable, Generator
from pathlib import Path
from typing import Any

from reemote5 import (
    Command,
    CommandError,
    FileContents,
    Host,
    HostConnectionError,
    InventoryError,
    Locales,
    OpContext,
    OperationRequest,
    PrivilegeError,
    StringCommand,
    files,
    format_log,
    format_plan,
    gather_fact,
    op,
    run,
    validate_inventory,
)

# The locale to ensure, and whether it should be enabled (pyinfra's
# documented example locale). present=False comments the definition line
# out instead; the skip gate and locale-gen step are identical.
LOCALE = "fr_FR.UTF-8"
PRESENT = True

# pyinfra's hard-coded definitions file, and the spool path: the basename
# ("locale.gen") is what files.move appends to /etc, landing the target.
LOCALE_GEN_PATH = "/etc/locale.gen"
SPOOL_PATH = "/tmp/locale.gen"

# Per-host spool transforms, filled ONCE in main between the snapshots and
# run 1: each is a stable callable closing over frozen desired content, so
# both phases of run 1 pass the identical object (OperationRequest compares
# the transform by value; a fresh closure per phase raises DeterminismError).
SPOOL_TRANSFORMS: dict[str, Callable[[str | None], str]] = {}

# examples/modules/server/server_op_locale.py -> the project root, so the
# inventory is found whatever the current working directory is. This recipe
# needs /etc/locale.gen + locale-gen (Debian/Ubuntu only), so it guards on
# the "ubuntu" group; the CentOS web1/web2 hosts have neither. No
# ``privilege`` entry is needed: the elevated install assumes passwordless
# sudo for the login user; a password would come from ``privilege`` in the
# inventory, never the op.
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


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"ubuntu"``.

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision. The recipe
    is Debian-family only, so the guard keeps it (and the facts it reads)
    off the Alpine and CentOS hosts.
    """
    return host.group == "ubuntu"


def locale_edit(content: str | None, locale: str, present: bool) -> str:
    """Pure locale.gen edit: comment out or uncomment the locale's line.

    Mirrors pyinfra's ``server.locale`` body: the definition line is the
    one matching ``<locale>`` followed by whitespace (pyinfra's grep
    ``^.*{locale}[[:space:]]\\+.*$``, with the locale escaped literally
    here), every other line is preserved, and zero or multiple matches
    raise ``ValueError`` with pyinfra's ``OperationError`` messages --
    including an absent file (``None`` content), which pyinfra's
    ``FindInFile`` reports as no matches.

    ``present=True`` strips every ``"# "`` occurrence from the line
    (pyinfra's ``str.replace`` verbatim); ``present=False`` prepends
    ``"# "``. The result always ends in one trailing newline.
    """
    pattern = re.compile(rf"{re.escape(locale)}[ \t\r\f\v]+")
    lines = (content or "").splitlines()
    matches = [i for i, line in enumerate(lines) if pattern.search(line)]
    if not matches:
        raise ValueError(f"Locale {locale} not found in {LOCALE_GEN_PATH}")
    if len(matches) > 1:
        raise ValueError(
            f"Multiple locales matches for {locale} in {LOCALE_GEN_PATH}"
        )
    index = matches[0]
    line = lines[index]
    lines[index] = line.replace("# ", "") if present else f"# {line}"
    return "\n".join(lines) + "\n"


def locale_needed(installed: list[str], locale: str, present: bool) -> bool:
    """pyinfra's change condition on the ``Locales`` fact snapshot.

    Enable only when the locale is absent from ``locale -a``, disable only
    when it is present -- the fact comparison that makes pyinfra's
    operation idempotent, moved to the gap between runs. The ``Locales``
    fact already normalizes ``utf8`` to ``UTF-8``, so the names compare
    against ``/etc/locale.gen`` spellings directly.
    """
    return locale not in installed if present else locale in installed


def make_transform(content: str) -> Callable[[str | None], str]:
    """Build a spool transform returning the frozen desired ``content``.

    Called once per host between the snapshots and run 1; the returned
    callable ignores the spool file's current text (the desired content was
    already computed from the real /etc/locale.gen) and is trivially a
    fixpoint.
    """

    def transform(current: str | None) -> str:
        return content

    return transform


async def locale_gen(ctx: OpContext, **kwargs) -> AsyncGenerator[Command]:
    """Regenerate the installed locales (demo-side ``locale-gen`` step).

    pyinfra's ``server.locale`` yields ``locale-gen`` after either edit;
    reemote5 ships no generic shell operation, so this example defines the
    stateless one-liner itself over the public raw ``StringCommand``
    descriptor. Like pyinfra's step it reads no fact and always reports a
    change; the ``Locales`` skip gate upstream of it decides whether it
    runs at all. The command needs root, so flag the request ``sudo=True``.
    ``**kwargs`` absorbs pyinfra's global arguments (``op()`` already
    strips ``name``/``sudo``/``su``).
    """
    ctx.changed = True
    ctx.reason = "will regenerate the installed locales with locale-gen"
    yield StringCommand("locale-gen")


def spool(host: Host) -> Generator[OperationRequest]:
    """Run 1: write the desired locale.gen content to the writable spool."""
    yield op(
        files.edit,
        name="Spool locale.gen",
        path=SPOOL_PATH,
        transform=SPOOL_TRANSFORMS[host.name],
        when=is_ubuntu,
    )


def install(host: Host) -> Generator[OperationRequest]:
    """Run 2: install the spool file over /etc/locale.gen, then regenerate.

    Both requests are elevated shell text; the lockstep barrier completes
    every host's move before any host runs ``locale-gen``.
    """
    yield op(
        files.move,
        name="Install locale.gen",
        src=SPOOL_PATH,
        dest="/etc",
        overwrite=True,
        sudo=True,
        when=is_ubuntu,
    )
    yield op(
        locale_gen,
        name="Regenerate locales",
        sudo=True,
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # The snapshots: read every guarded host's real /etc/locale.gen and
        # installed locales (read-only cat / locale -a). gather_fact has no
        # ``when`` parameter, so the is_ubuntu predicate filters the host
        # list before the fan-out -- a skipped host is never connected.
        all_hosts = [Host.from_inventory(entry) for entry in inventory]
        hosts = [host for host in all_hosts if is_ubuntu(host)]
        skipped = [host.name for host in all_hosts if host not in hosts]
        if skipped:
            print("\n== Skipped (when guard is_ubuntu): " + ", ".join(skipped) + " ==")
        try:
            contents = await gather_fact(hosts, FileContents, LOCALE_GEN_PATH)
            installed = await gather_fact(hosts, Locales)
        finally:
            for host in hosts:
                await host.aclose()

        # Controller-side idempotence: pyinfra's Locales change condition
        # on the frozen snapshot; only pending hosts get a transform. Only
        # guarded hosts carry a snapshot entry; the rest stay skipped.
        pending = []
        for entry in inventory:
            name = entry["host"]
            if name not in installed:
                continue
            if not locale_needed(installed[name], LOCALE, PRESENT):
                continue
            desired = locale_edit(contents[name], LOCALE, PRESENT)
            SPOOL_TRANSFORMS[name] = make_transform(desired)
            pending.append(entry)
        if not pending:
            state = "enabled" if PRESENT else "disabled"
            print(f"All hosts already have {LOCALE} {state}.")
            return

        # Run 1: spool the transformed content to /tmp/locale.gen,
        # unprivileged.
        plans, _, _hosts = await run(pending, spool, dry=True)
        print("== Run 1 plan: spool locale.gen (--dry) ==")
        print(format_plan(plans))

        plans, report, _hosts = await run(pending, spool)
        print("\n== Run 1 log: spool locale.gen ==")
        assert report is not None
        print(format_log(report))

        # Run 2: the spool file now really exists, so the move's validation
        # passes in the prepare phase too. Elevated: /etc is root-owned,
        # and locale-gen writes the system locale archive.
        plans, _, _hosts = await run(pending, install, dry=True)
        print("\n== Run 2 plan: install locale.gen + locale-gen (--dry) ==")
        print(format_plan(plans))

        plans, report, _hosts = await run(pending, install)
        print("\n== Run 2 log: install locale.gen + locale-gen ==")
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