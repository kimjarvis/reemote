# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: create, adjust and remove a user account on real CentOS hosts.

This is a *usage* example, not part of the library. It shows reemote5's
``server.user`` operation, baseline ``server.user`` parity for user
accounts: the deploy creates a ``deploy`` service account with a login
shell and a GECOS comment, puts it in the ``apps`` group, then *appends*
the ``staff`` group to it, and finally removes a ``legacy`` account that
is no longer wanted -- all under ``sudo``, because ``useradd``,
``usermod``, ``userdel`` and the home-directory ``mkdir``/``chown`` pair
all need root.

The operation is idempotent: it reads the ``Users`` fact (every
``/etc/passwd`` row joined with ``id``'s groups, the ``lastlog`` row and the
shadow hash) and yields a command only where the account differs from the
request -- ``useradd`` for an
absent user, a ``usermod`` carrying just the differing fields for a
present one, ``userdel`` for ``present=False``. A second run therefore
plans no change. Two details are worth seeing in the plan:

* the ``ensure_home`` branch (on by default) is the baseline's delegation to
  ``files.directory``, inlined as ``mkdir -p /home/deploy`` plus ``chown
  deploy:deploy /home/deploy`` when the home is missing, a plain
  ``chown`` when only the owner differs, and no command at all when the
  home is a symlink. ``create_home=False`` here means ``useradd`` builds
  nothing from the skeleton, so that pair is what actually creates the
  directory;
* the append step shows the per-phase fact cache. In the *plan* both user
  requests read the same pre-deployment state, so both show a change; in
  the *apply* each step re-reads the facts, so step 4 sees the groups
  step 3 wrote and sends only ``usermod -a -G staff``.

``groups`` is passed as a tuple: every ``op()`` argument must stay
hashable for ``compose(dedupe=True)``, so a list would be rejected with
``UnhashableArgumentError``. The two secondary groups are created first
by ``server.group`` requests, which the lockstep barrier completes before
the account is touched. The baseline's ``public_keys``/``delete_keys`` options
are deliberately dropped -- that branch rides ``files.line`` appends and
an elevated ``files.put``, neither of which reemote5 has -- so the script
manages no ``authorized_keys`` file; the baseline's plan-time ``Which`` guards
are dropped too, so a missing ``useradd`` surfaces at apply as
``CommandNotFoundError`` from the exit status. ``password`` is also left
unset here: it takes a pre-encrypted shadow hash, which a shared demo
inventory should not carry.

Unlike the pytest suite -- which injects the test framework's
``SimulatedHost`` via ``run``'s ``host_factory`` and never touches the
network -- this example uses the *default* factory
(``Host.from_inventory``): each plain ``Host`` lazily opens an asyncssh
connection, reads ``/etc/passwd``, ``/etc/group``, ``/etc/shadow``,
``uname -s`` and the home directory status for real, and runs the account
commands under sudo where needed.

Every failure mode of the run -- an unreachable host, a rejected
elevation, an ``OperationError`` for a home path that exists as something
other than a directory, or a ``useradd``/``usermod`` that exits non-zero
(a group that turns out to be missing, for instance) -- reaches ``main``
as a typed error whose message already names the host, so ``main`` catches
them and prints one line each instead of a traceback.

Note that this script mutates the shared demo inventory: the ``deploy``
account and its home directory stay on the hosts (and the ``apps``/
``staff`` groups with them), while ``legacy`` is removed wherever it
existed. Re-running it is a no-op.

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

    uv run python examples/modules/server/server_op_user.py
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

# examples/modules/server/server_op_user.py -> the project root, so the inventory
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


def deploy(host: Host) -> Generator[OperationRequest]:
    """Create the deploy account, append a group to it, remove the legacy one.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases; whether each account already exists -- and which of its
    fields differ -- is decided inside the operation against the freshly
    gathered ``Users`` fact, per phase.
    """
    # Prerequisites first: the lockstep barrier finishes both group steps
    # before any host touches an account, so ``-G apps`` and the appended
    # ``-G staff`` always name groups that exist.
    yield op(
        server.group,
        name="Create the apps group",
        group="apps",
        sudo=True,
        when=is_centos,
    )
    yield op(
        server.group,
        name="Create the staff group",
        group="staff",
        sudo=True,
        when=is_centos,
    )
    # An absent account: ``useradd`` plus, through ``ensure_home``, the
    # ``mkdir -p``/``chown`` pair for ``/home/deploy``. A present one gets
    # only the fields that differ.
    yield op(
        server.user,
        name="Create the deploy account",
        user="deploy",
        shell="/bin/bash",
        comment="Deployment service account",
        groups=("apps",),
        sudo=True,
        when=is_centos,
    )
    # ``append=True`` adds memberships without removing any: in the apply
    # phase the account already has ``apps``, so only ``staff`` rides the
    # ``usermod -a -G``.
    yield op(
        server.user,
        name="Append the staff group to the deploy account",
        user="deploy",
        groups=("staff",),
        append=True,
        sudo=True,
        when=is_centos,
    )
    # The mirror image: ``userdel`` only where the account exists, and no
    # home directory is touched -- neither ``userdel`` nor the baseline's branch
    # removes files.
    yield op(
        server.user,
        name="Remove the legacy account",
        user="legacy",
        present=False,
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The account list, the kernel name and
        # the home directory status are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no
        # account is created, changed or removed.
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
    except OperationError as exc:
        # The requested home path already exists as something other than a
        # directory (a plain file, say). The prepare phase aborted before
        # anything was sent; the message names the host and the path.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except PrivilegeError as exc:
        # The host was reached but rejected the ``sudo=True`` elevation -- a
        # wrong ``sudo_password``, or a run-as user the login user may not
        # become. One line naming the host; the detail stays on ``__cause__``.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except CommandError as exc:
        # An account command exited non-zero (``usermod`` refusing a group
        # that does not exist, for instance). The typed message already names
        # the host, the credential-free command and its stderr, so print just
        # that.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
