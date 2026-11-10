# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Set a variable in /etc/rc.conf with ``openrc.config``.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Read the target hosts from ``inventory.json`` in the project root -- an
   inventory is just a list of dictionaries, each holding a host's name plus
   its SSH ``connection`` information (address, username and client key),
   which is exactly what JSON expresses -- and validate it up front with
   ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is
   materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects, each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``). Each plain ``Host``
lazily opens an asyncssh connection from its ``connection`` data, gathers
facts and runs commands for real, and ``run`` closes those connections when
it finishes. The path is resolved from ``__file__``, so the example works from
any working directory; point ``INVENTORY_PATH`` at your own file to target
your machines.

``run`` is a coroutine (both phases are async), so ``main`` is async and
driven by ``asyncio.run``.

``openrc.config`` is a reemote5 original with no pyinfra counterpart
(pyinfra's ``openrc`` namespace ships only ``service``). It sets or unsets
one shell variable in ``/etc/rc.conf`` -- or, with ``service="<name>"``, in
that service's ``/etc/conf.d/<name>`` file -- and belongs to the
spool-and-install write family. The file's current text is read with the
``OpenrcConfigFile`` content fact, a pure transform drops every existing
assignment of the key (commented-out ones included, so no contradictory
commented line survives) and appends ``key="value"``, and when the result
differs from what is there the request yields *two* commands at two
privilege levels:

* an unprivileged scp transfer spooling the whole transformed text to a
  ``/tmp`` path derived from the managed file's sha1 -- reemote5's
  transfers never elevate, which is *why* the root-owned ``/etc/rc.conf``
  cannot be written directly and the content detours through ``/tmp``;
* an elevated ``sh -c 'mkdir -p ... && cp ... && chmod 0644 ... &&
  rm -f <spool>'`` installing the spooled text over the protected file and
  removing the spool, every step of the chain running as root.

The operation is idempotent on the transform's fixpoint: a file whose only
assignment of the key already carries the wanted value plans no change, so
re-running this script after a successful install is a no-op. The variable
ensured here is ``rc_parallel="YES"``, OpenRC's parallel startup switch: it
is read when a runlevel is entered, so the change takes effect at the next
boot (or ``openrc default`` re-entry) -- unlike a sysctl there is no runtime
half to apply, the file edit is the whole job. Point ``service`` at a
service name to manage its ``/etc/conf.d`` options the same way, and flip
``present`` to False to unset a key (every assignment of it is dropped).
Validation raises ``ValueError`` in the prepare phase before anything is
sent: the key must be a shell identifier, and the value must not contain
``"``, ``\\``, ``$``, a backtick or a newline, which would break out of the
double-quoted rendering when OpenRC sources the file.

The *same* request is yielded twice to show idempotence across the two
phases: the prepare phase plans both as changes when the value is not yet
set (nothing is sent in prepare, so both compare against the same
pre-deployment state), while the apply phase installs on the first and
reports the second as a no-op, because the fresh per-step fact cache now
reads the content the first step installed.

The install writes root-owned ``/etc``, so the requests are flagged
``doas=True`` and the driver elevates that command -- the scp half stays
unprivileged regardless. ``doas`` rather than ``sudo`` because the Alpine
host has no ``sudo`` installed at all -- ``sudo sh -c`` would die with
``sh: sudo: not found`` (exit 127). ``doas`` is passwordless here: reemote5
always runs ``doas -n``, which fails rather than prompts, so the host must
grant the login user a ``permit nopass`` rule in ``/etc/doas.conf`` (see
``examples/demonstrations/privilege/privilege_doas.py``); without one the
driver raises ``PrivilegeError``.

Every request also carries ``when=is_alpine``, the declarative eligibility
guard (see
``examples/demonstrations/composition/composed_when_guard.py``). Two rules
make it deterministic: the predicate reads only **static** inventory data --
``host.group``, set by the ``"group"`` key of the inventory entry -- never a
mutable fact, and it is a stable module-level function, so the prepare and
apply phases reach the same skip decision. ``when`` is a meta-parameter like
``doas``: captured on the request, never forwarded to the operation, and
skipped by the *driver*, so every host keeps the identical two-request
schedule (no ``if`` in the deploy body) and each request still consumes its
lockstep slot. On a host the guard rejects, the plan and the run log render
the step as ``[skipped: when guard is_alpine]`` and none of its commands --
nor the ``OpenrcConfigFile`` fact behind them -- is materialized at all, so
``/etc/rc.conf`` is never even read on the systemd hosts the shared
inventory also holds.

Run it with::

    uv run python examples/modules/openrc/openrc_config.py
"""

import asyncio
import json
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    PrivilegeError,
    format_log,
    format_plan,
    op,
    openrc,
    run,
    validate_inventory,
)

# examples/modules/openrc/openrc_config.py -> the project root, so the
# inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The setting to ensure: OpenRC's parallel startup switch, read when a
# runlevel is entered (so the edit takes effect at the next boot).
RC_KEY = "rc_parallel"
RC_VALUE = "YES"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of inventory entries -- dictionaries
    each carrying at least ``host`` and ``connection``. Returns the parsed
    list, ready to hand to ``run``, which materializes it with the default
    ``Host.from_inventory`` factory; raises ``InventoryError`` (via
    ``validate_inventory``) when the document is not an array or an entry
    does not match the schema.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_alpine(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"alpine"``.

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision and the
    determinism guard holds. OpenRC itself is the reason for the guard:
    ``/etc/rc.conf`` is OpenRC's file and must not be read, let alone
    rewritten, on the systemd hosts the shared inventory also holds.
    """
    return host.group == "alpine"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Set rc_parallel="YES" in /etc/rc.conf -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError),
    and identical on every host: eligibility is expressed with the
    ``when=is_alpine`` guard each request carries, not with an ``if``. The
    first request spools and installs the transformed rc.conf if the value
    is not yet set; the second carries the same arguments and is a
    guaranteed no-op in the apply phase, because the installed content is
    already visible through the fresh per-step fact cache.

    The install writes root-owned /etc, so both requests are flagged
    ``doas=True`` (the host has no ``sudo``); the scp spool half stays
    unprivileged regardless -- transfers never elevate.
    """
    yield op(
        openrc.config,
        name="Enable parallel startup",
        key=RC_KEY,
        value=RC_VALUE,
        doas=True,
        when=is_alpine,
    )
    yield op(
        openrc.config,
        name="Re-set rc_parallel (no-op)",
        key=RC_KEY,
        value=RC_VALUE,
        doas=True,
        when=is_alpine,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no command
        # that mutates a host is sent. A request the ``when`` guard rejects is
        # planned as [skipped: when guard is_alpine] without materializing it,
        # so its fact is not read on that host either.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("\n== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Run log (apply phase) ==")
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