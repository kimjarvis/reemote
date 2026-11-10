# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: ensure a resource limit (pyinfra server.security_limit parity).

This is a *usage* example, not part of the library. It shows reemote5's
``server.security_limit`` operation, the pyinfra ``server.security_limit``
parity for ``/etc/security/limits.conf``: the deploy ensures every guarded
host carries ``* soft nofile 1024``, under ``sudo`` because the limits file
is root-owned and ``pam_limits`` reads it as every user.

``server.security_limit`` is a **spool-and-install** operation, and it
replaces the earlier ``files.edit`` + demo-side ``limits_install`` example
recipe that used to live in this file: the fact read, the line edit and the
install now happen inside one operation, so a single deploy suffices. In one
request it reads the raw ``/etc/security/limits.conf`` text through the
``AuthorizedKeysFile`` content fact, applies pyinfra's anchored ``files.line``
lookup on the controller (the value is NOT part of the pattern, so a stale
value is normalized in place), scp's the transformed text to a deterministic
``/tmp`` spool (the transfer rides the login user -- transfers never elevate),
and installs it over ``/etc/security/limits.conf`` with an elevated ``sh -c``
(``chmod 0644``, spool removed). The log shows the family's two privilege
levels: an unprivileged ``$ scp <limits.conf> -> ...`` line followed by
``$ sudo sh -c '...'``.

The operation is idempotent at the content level -- pyinfra's ``files.line``
no-op when a matching line already reads exactly the replacement: a host whose
limits.conf already carries the line plans no change, and a second run is a
no-op everywhere. One documented deviation: the desired content always ends in
one trailing newline, so a file missing its final newline is rewritten once
even when the line itself is correct. The match regex escapes domain, type and
item literally (``re.escape``), the hardening over pyinfra's raw BRE
interpolation; an item prefix like ``nofilex`` matches (pyinfra's ``.*$``) while
a commented-out line does not (the anchor). The ``value`` accepts both an
``int`` and a symbolic string such as ``"unlimited"`` (pyinfra types it ``int``
but its own fixture passes ``"unlimited"``).

Every failure mode of the run -- an unreachable host, a rejected elevation, or
an install step that exits non-zero -- reaches ``main`` as a typed error whose
message already names the host, so ``main`` catches them and prints one line
each instead of a traceback.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``): each plain ``Host``
lazily opens an asyncssh connection, reads ``/etc/security/limits.conf`` for
real, and installs the limit under sudo where needed.

Every request carries ``when=is_centos``, the declarative eligibility guard:
the predicate reads only **static** inventory data -- ``host.group``, set by
the ``"group"`` key of the inventory entry -- and is a stable module-level
function, so the prepare and apply phases reach the same skip decision. The
driver skips a rejected request without materializing it, so on hosts outside
the ``"centos"`` group neither the commands nor the facts behind them run, and
plan and log render the step as ``[skipped: when guard is_centos]`` in its
lockstep slot.

The path is resolved from ``__file__``, so the example works from any working
directory; point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/server/server_security_limit.py
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
    run,
    server,
    validate_inventory,
)

# The limit to ensure: pyinfra's documented example ("Set nofile limit for
# all users"). The value is an int here; the operation also accepts a symbolic
# string such as "unlimited".
DOMAIN = "*"
LIMIT_TYPE = "soft"
ITEM = "nofile"
VALUE = 1024

# examples/modules/server/server_security_limit.py -> the project root, so the
# inventory is found whatever the current working directory is.
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


def deploy(host: Host) -> Generator[OperationRequest]:
    """Ensure every guarded host carries the nofile limit, under sudo.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases; whether the limit line is already present -- and whether
    its value is current -- is decided inside the operation against the
    freshly gathered content fact, per phase.
    """
    yield op(
        server.security_limit,
        name="Set nofile limit for all users",
        domain=DOMAIN,
        limit_type=LIMIT_TYPE,
        item=ITEM,
        value=VALUE,
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The /etc/security/limits.conf text is
        # still gathered over SSH (read-only) so the plan reflects each
        # host's real pre-deployment state; nothing is uploaded or installed.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. Each host
        # gets its limit line set where it differs; a second run plans no
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
    except PrivilegeError as exc:
        # The host was reached but rejected the ``sudo=True`` elevation -- a
        # wrong ``sudo_password``, or a run-as user the login user may not
        # become. One line naming the host; the detail stays on ``__cause__``.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except CommandError as exc:
        # The install step exited non-zero. The typed message already names
        # the host, the credential-free command and its stderr, so print
        # just that.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())