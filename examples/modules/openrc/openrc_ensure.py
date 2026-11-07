# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Install a package and ensure its service by composing apk + openrc.

This is a *usage* example, not part of the library. It is reemote5's answer
to the proposed ``openrc.ensure`` operation: no combined install-and-enable
operation exists, because the two halves already compose in a deploy --
``apk.packages`` installs the package, ``openrc.service`` enables and
starts the service it ships -- and the driver's lockstep execution gives
the composition the ordering guarantee a bespoke operation would have to
build itself.

It shows how an application drives reemote5's two-phase execution model
end to end over a **real SSH transport** (asyncssh):

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

The pair is demonstrated on ``nginx``: Alpine's package ships the
``/etc/init.d/nginx`` script, so installing it is what makes the service
manageable at all. Request 1 (``apk.packages``) reads the
``ApkInstalledPackages`` fact and runs ``apk add nginx`` only when the
package is missing; request 2 (``openrc.service``) reads the
``OpenrcStatus``/``OpenrcEnabled`` facts and starts the service
(``rc-service nginx start``) and assigns it to the default runlevel
(``rc-update add nginx default``) only where each is missing. Each half is
idempotent against its own facts -- the composition inherits that, with no
combined fact or "ensure" state machine to maintain.

The ordering is the point: the apply phase runs in *lockstep*, one request
index at a time across all hosts -- every host finishes the install step
before any host begins the service step -- so the service request always
sees the init script the install step just delivered, and its fresh
per-step fact cache reads the post-install state. That is why the pair can
live in one deploy while the sysctl recipe's edit-then-move needs two
runs: nothing here validates a controller-side artifact between steps
(``files.move``'s prepare-phase ``src`` check is what forces that split).

The script then yields the *same pair again* to show idempotence across
the two phases: the prepare phase plans the repeats as changes when nginx
is absent (nothing is sent in prepare, so every request compares against
the same pre-deployment state -- the service half even plans against a
host where nginx is not installed yet), while the apply phase reports both
repeats as no-ops, because the fresh per-step fact caches now see what the
first pair produced. Re-running the whole script later is likewise a
no-op.

Both halves write root-owned state (the apk database, the service and
runlevel trees), so every request is flagged ``doas=True`` and the driver
elevates their commands. ``doas`` rather than ``sudo`` because the Alpine
host has no ``sudo`` installed at all -- ``sudo apk add`` would die with
``sh: sudo: not found`` (exit 127) before reaching apk. ``doas`` is
passwordless here: reemote5 always runs ``doas -n``, which fails rather
than prompts, so the host must grant the login user a ``permit nopass``
rule in ``/etc/doas.conf`` (see ``examples/demonstrations/privilege/
privilege_doas.py``); without one the driver raises ``PrivilegeError``.

Every request also carries ``when=is_alpine``, the declarative eligibility
guard (see
``examples/demonstrations/composition/composed_when_guard.py``). Two rules
make it deterministic: the predicate reads only **static** inventory data --
``host.group``, set by the ``"group"`` key of the inventory entry -- never a
mutable fact, and it is a stable module-level function, so the prepare and
apply phases reach the same skip decision. ``when`` is a meta-parameter like
``doas``: captured on the request, never forwarded to the operation, and
skipped by the *driver*, so every host keeps the identical four-request
schedule (no ``if`` in the deploy body) and each request still consumes its
lockstep slot. On a host the guard rejects, the plan and the run log render
the step as ``[skipped: when guard is_alpine]`` and none of its commands --
nor the facts behind it -- is materialized at all: neither apk nor OpenRC
exists on the systemd hosts the shared inventory also holds, so the guard
is what keeps both halves off them.

Run it with::

    uv run python examples/modules/openrc/openrc_ensure.py
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
    apk,
    format_log,
    format_plan,
    op,
    openrc,
    run,
    validate_inventory,
)

# examples/modules/openrc/openrc_ensure.py -> the project root, so the
# inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The package to install, as a tuple -- OperationRequest kwargs must stay
# hashable -- and the service it ships. Alpine's nginx package installs
# /etc/init.d/nginx, so the service name matches the package name here.
PACKAGES = ("nginx",)
SERVICE = "nginx"


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
    """``when`` guard: the host belongs to the inventory group ``"alpine"".

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision and the
    determinism guard holds. Both halves need the guard: a non-Alpine host
    has neither ``apk`` nor OpenRC, so every request below would fail on a
    missing binary and the driver skips them all instead.
    """
    return host.group == "alpine"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Install nginx then ensure its service -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError),
    and identical on every host: eligibility is expressed with the
    ``when=is_alpine`` guard each request carries, not with an ``if``. The
    lockstep apply runs the install on every host before the service
    request runs anywhere, so the service half always sees the init script
    the install delivered. The repeated pair is a guaranteed no-op in the
    apply phase, because each half's fresh per-step fact cache sees the
    state the first pair produced.

    Both halves write root-owned state, so all requests are flagged
    ``doas=True`` (the host has no ``sudo``) and the driver elevates their
    commands over SSH with passwordless ``doas -n``.
    """
    yield op(
        apk.packages,
        name="Install nginx",
        packages=PACKAGES,
        doas=True,
        when=is_alpine,
    )
    yield op(
        openrc.service,
        name="Ensure nginx running and enabled",
        service=SERVICE,
        running=True,
        enabled=True,
        doas=True,
        when=is_alpine,
    )
    yield op(
        apk.packages,
        name="Re-install nginx (no-op)",
        packages=PACKAGES,
        doas=True,
        when=is_alpine,
    )
    yield op(
        openrc.service,
        name="Re-ensure nginx (no-op)",
        service=SERVICE,
        running=True,
        enabled=True,
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
        # so its facts are not read on that host either.
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