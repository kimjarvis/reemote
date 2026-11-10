# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: enable a locale on real Ubuntu hosts (Debian-family only).

This is a *usage* example, not part of the library. It shows reemote5's
``server.locale`` operation, baseline ``server.locale`` parity for
``/etc/locale.gen``: the deploy ensures every host has ``fr_FR.UTF-8``
enabled, under ``sudo`` because the definitions file is root-owned and
``locale-gen`` writes the system locale archive.

``server.locale`` is a **spool-and-install** operation, and it replaces the
earlier two-run ``files.edit`` -> ``files.move`` example recipe that used to
live in this file: the fact reads, the line edit, the install and the
regeneration now happen inside one operation, so a single deploy suffices.
In one request it reads the ``Locales`` fact (``locale -a``) and the raw
``/etc/locale.gen`` text, uncomments the locale's definition line on the
controller, scp's the transformed text to a deterministic ``/tmp`` spool
(the transfer rides the login user -- transfers never elevate), installs it
over ``/etc/locale.gen`` with an elevated ``sh -c`` (``chmod 0644``, spool
removed), and finishes with one elevated ``locale-gen``. The log shows the
family's two privilege levels: an unprivileged ``$ scp <locale.gen> -> ...``
line followed by ``$ sudo sh -c '...'`` and ``$ sudo locale-gen``.

The operation is idempotent: it plans a change only where the ``Locales``
listing disagrees with the request, so a host with the locale already
generated plans no change and a second run is a no-op everywhere. When the
definition line is already uncommented but the archive has not been
generated yet, the write is skipped and only the ``locale-gen`` step is
planned. It is supported on **Debian-family systems only**: a host with no
``/etc/locale.gen`` (RHEL-family systems install locales through
``glibc-langpack-*``) or no matching definition line raises
``OperationError`` during the prepare phase, before anything is sent --
the baseline's own behaviour.

Every failure mode of the run -- an unreachable host, a rejected elevation,
a missing or ambiguous definition line, or a ``locale-gen`` that exits
non-zero -- reaches ``main`` as a typed error whose message already names
the host, so ``main`` catches them and prints one line each instead of a
traceback.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``): each plain ``Host``
lazily opens an asyncssh connection, reads ``locale -a`` and
``/etc/locale.gen`` for real, and enables the locale under sudo where
needed.

Every request carries ``when=is_ubuntu``, the declarative eligibility
guard: the predicate reads only **static** inventory data --
``host.group``, set by the ``"group"`` key of the inventory entry -- and
is a stable module-level function, so the prepare and apply phases reach
the same skip decision. The driver skips a rejected request without
materializing it, so on hosts outside the ``"ubuntu"`` group neither the
commands nor the facts behind them run, and plan and log render the step
as ``[skipped: when guard is_ubuntu]`` in its lockstep slot.

The path is resolved from ``__file__``, so the example works from any
working directory; point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/server/server_op_locale.py
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
    OperationError,
    OperationRequest,
    PrivilegeError,
    format_log,
    format_plan,
    op,
    run,
    server,
    validate_inventory,
)

# examples/modules/server/server_op_locale.py -> the project root, so the inventory
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


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"ubuntu"``.

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision.
    """
    return host.group == "ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Ensure every host has fr_FR.UTF-8 enabled, under sudo.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases; whether the locale is already generated -- and whether
    its definition line exists at all -- is decided inside the operation
    against the freshly gathered facts, per phase.
    """
    yield op(
        server.locale,
        name="Enable French locale",
        locale="fr_FR.UTF-8",
        sudo=True,
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The Locales listing and the
        # /etc/locale.gen text are still gathered over SSH (read-only) so
        # the plan reflects each host's real pre-deployment state; nothing
        # is uploaded or installed.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. Each host
        # gets its locale enabled where it differs; a second run plans no
        # change.
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
    except HostConnectionError as exc:
        # A host could not be reached over SSH at all. Report it plainly --
        # naming every unreachable host -- instead of dumping an asyncssh
        # traceback, and exit non-zero so the failure is script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except OperationError as exc:
        # The locale's definition line is missing (a RHEL-family host with
        # no /etc/locale.gen at all) or ambiguous. The prepare phase aborted
        # before anything was sent; the message names the host and the
        # lookup failure.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except PrivilegeError as exc:
        # The host was reached but rejected the ``sudo=True`` elevation -- a
        # wrong ``sudo_password``, or a run-as user the login user may not
        # become. One line naming the host; the detail stays on ``__cause__``.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except CommandError as exc:
        # An install step or ``locale-gen`` exited non-zero (a broken
        # locale definition, for instance). The typed message already names
        # the host, the credential-free command and its stderr, so print
        # just that.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())