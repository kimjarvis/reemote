# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: manage an /etc/hosts entry with server.etc_hosts.

This is a *usage* example, not part of the library. It shows reemote's
``server.etc_hosts`` operation, baseline ``server.etc_hosts`` parity:
the deploy ensures the line keyed by an IP address holds exactly the
requested hostnames, rewriting it in place when the names differ and
appending it when the IP is unknown. It replaces the earlier two-run
``files.edit`` -> ``files.move`` example recipe: the fact read, the pure
hosts-file transform and the install now live in one operation, so a
single deploy suffices -- no snapshot, no transform factory, no barrier
between "spool exists" and "move validates it".

The operation is idempotent and reads two facts. The parsed ``EtcHosts``
fact maps each IP to its hostnames; when the entry already matches, the
request is a no-op with the baseline's own reason ("already present in",
"already absent from", "does not reference any of:"). When a change is
due, the ``AuthorizedKeysFile`` raw-content fact -- the server namespace's
path-generic content reader -- supplies the file's current text and a
pure controller-side transform computes the new whole content, preserving
every untouched line, comment and blank line (comments on the edited line
are dropped, baseline parity).

The write is a spool-and-install pair: the transformed text rides an
unprivileged scp to a ``/tmp`` spool whose name is derived from the managed
path, then an ``sh -c 'mkdir -p ... && cp ... && chmod 0644 ... && rm -rf ...'``
install overlays the hosts file and removes the spool directory. The scp half stays
unprivileged even under ``sudo=True`` -- transfers never elevate -- so the
log shows two privilege levels in one request: a ``$ scp <hosts file> -> ...``
line followed by a ``$ sudo sh -c '...'`` line. ``/etc/hosts`` is root-owned,
so both requests here carry ``sudo=True``.

``hostnames`` is a tuple: every ``op()`` argument must stay hashable for
``compose(dedupe=True)``, so a list would be rejected with
``UnhashableArgumentError``. A single string works too (it is split on
whitespace); ``present=False`` with hostnames drops only those names, and
without hostnames removes every line for the IP.

Unlike the pytest suite -- which injects the test framework's
``SimulatedHost`` via ``run``'s ``host_factory`` and never touches the
network -- this example uses the *default* factory (``Host.from_inventory``):
each plain ``Host`` lazily opens an asyncssh connection, reads the real
``/etc/hosts`` for the fact comparison and the content transform, and runs
the spool-and-install pair only where the entry differs.

Every failure mode of the run -- an unreachable host, a rejected elevation,
a ``ValueError`` for ``present=True`` without hostnames, or a command that
exits non-zero -- reaches ``main`` as a typed error whose message already
names the host (or the argument), so ``main`` catches them and prints one
line each instead of a traceback.

Note that this script mutates the target hosts' ``/etc/hosts`` for real:
the entry stays in place. Re-running it is a no-op, because the ``EtcHosts``
fact already reports the requested names.

Every request carries ``when=is_centos``, the declarative eligibility
guard: the predicate reads only **static** inventory data --
``host.group``, set by the ``"group"`` key of the inventory entry -- and
is a stable module-level function, so the prepare and apply phases reach
the same skip decision. The driver skips a rejected request without
materializing it, so on hosts outside the ``"centos"`` group neither the
commands nor the facts behind them run, and plan and log render the step
as ``[skipped: when guard is_centos]`` in its lockstep slot.

The path is resolved from ``__file__``, so the example works from any
working directory; point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/server/server_etc_hosts.py
"""

import asyncio
import json
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    PrivilegeError,
    format_log,
    format_plan,
    op,
    run,
    server,
    validate_inventory,
)

# examples/modules/server/server_etc_hosts.py -> the project root, so the
# inventory is found whatever the current working directory is. No
# ``privilege`` entry is needed: the elevated install assumes passwordless
# sudo for the login user; a password would come from ``privilege`` in the
# inventory, never the op.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The entry to ensure, keyed by IP exactly like the baseline's server.etc_hosts.
# A tuple, because ``op()`` arguments must stay hashable.
DB_IP = "10.0.0.5"
DB_HOSTNAMES = ("db.internal", "db")


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


def deploy(host: Host) -> Generator[OperationRequest]:
    """Ensure the database entry in /etc/hosts, elevated.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases; whether the entry already matches is decided inside the
    operation against the freshly gathered facts, per phase.
    """
    # First request: rewrite or append the line keyed by DB_IP so it holds
    # exactly DB_HOSTNAMES. /etc/hosts is root-owned, so sudo=True -- the
    # scp spool half stays unprivileged regardless, only the install shell
    # elevates.
    yield op(
        server.etc_hosts,
        name="Point the database names at the database server",
        ip=DB_IP,
        hostnames=DB_HOSTNAMES,
        sudo=True,
        when=is_centos,
    )
    # Second, identical request demonstrates the per-phase fact cache: in the
    # *plan* both requests read the same pre-deployment state, so both show a
    # change; in the *apply* each step re-reads the facts, so this step sees
    # the entry the first step wrote and yields nothing -- a no-op.
    yield op(
        server.etc_hosts,
        name="Ensure the same entry again (idempotence check)",
        ip=DB_IP,
        hostnames=DB_HOSTNAMES,
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The hosts file is still gathered over
        # SSH (read-only cat) so the plan reflects each host's real
        # pre-deployment state; nothing is spooled and nothing is installed.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. Each step
        # re-reads the facts, so a second run of this script plans no change
        # anywhere.
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
    except ValueError as exc:
        # Parameter validation: present=True without hostnames. Raised in
        # the prepare phase before anything is sent.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except PrivilegeError as exc:
        # The host was reached but rejected the elevation the install
        # needs. One line naming the host; the detail stays on __cause__.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except CommandError as exc:
        # The install command exited non-zero. The typed message already
        # names the host, the credential-free command and its stderr, so print
        # just that.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())