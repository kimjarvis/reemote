# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Run the ``users`` role against an inventory, end to end.

This is a *usage* example, not part of the library. The role itself lives in
:mod:`reemote.roles.users`; this script is the runnable artifact -- a plain
``main()`` that does what every reemote program does, with no shared runner
and no framework:

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information -- and validate it up
   front with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is
   materialized or connected.
2. Resolve the role's variables with ``resolve_vars``: the inventory entry's
   ``users`` role mapping (``groups_to_create``, ``users``, ``deleted``,
   ``create_per_user_group`` and the rest of the
   :class:`~reemote.roles.users.UsersVars` fields) is bound under the bare
   field names, the call-site ``USERS_OVERRIDES`` below wins over them, and a
   bad value raises before any host is contacted.
3. Build the deploy -- ``users(vars_set)`` is the role's whole task list, one
   per-host generator -- and drive it with ``run_waves``, the barrier-aware
   driver a role needs. The users role takes *no* barrier on this
   configuration (the one ``fact(Users)`` read only appears when a user
   combines ``update_password: on_create`` with a password), so its whole
   stream is a single request wave: groups, per-user groups, users and key
   pairs, authorized keys, profiles, and the deletions last. ``run_waves``
   owns the host connections and closes them itself.

**This example creates and removes real accounts.** The overrides below
manage a clearly namespaced demo user (``reemote-demo``) and delete a
likewise namespaced ``reemote-gone``; nothing you log in as is touched. On a
real host the run still needs root: every request carries ``sudo=True``,
because ``groupadd``/``useradd``/``usermod``/``userdel`` and any write into
another user's home are privileged (the transfers stay unprivileged -- the
spool-and-install family elevates only the final ``cp``).

Unlike the pytest suite -- which injects the test framework's
``SimulatedHost`` and never touches the network -- this example uses the
*default* factory (``Host.from_inventory``): each plain ``Host`` lazily opens
an asyncssh connection from its ``connection`` data, gathers facts and runs
commands for real. Edit ``inventory.json`` in the current working directory
to target your machines.

``run_waves`` is a coroutine, so ``main`` is async and driven by
``asyncio.run``. The factory itself is a plain (non-``async``) function, so
``users(vars_set)`` is *called*, not awaited.

Installing and running
----------------------

The role renders nothing: its profile block is plain text and every other
file is written by an operation, so no ``reemote[templates]`` extra is
needed. Run it with::

    uv run python examples/roles/users.py

``main`` drives the deploy twice, exactly like an operation example: a
``dry=True`` preview first, then the real apply. Because this configuration
has no barrier, the dry pass *plans the whole wave* and applies nothing. A
converged rerun is the honest preview: run the whole program a second time
and it reports no change anywhere -- the groups exist with their gids, the
demo account carries its shell, comment, home and empty secondary-group
membership, its key pair and authorized key are in place, the profile block
is byte-identical between its markers, and the deleted account is already
gone.

Hosts outside the ``"Ubuntu"`` family emit nothing at all -- the role's
family guard is its body's opening ``if``, so such a host returns before its
first ``yield`` and appears in no wave.
"""

import asyncio
import json
import sys
from pathlib import Path

from reemote import (
    CommandError,
    HostConnectionError,
    InventoryError,
    PrivilegeError,
    resolve_vars,
    run_waves,
    validate_inventory,
)
from reemote.roles import UsersVars, users

# Common role variables handed to ``resolve_vars`` as call-site ``overrides``.
# A namespaced demo account exercises the whole task list -- a pinned
# ``developers`` group, a per-user group, secondary-group stripping, a
# profile block, a generated key pair and an authorized key -- and one
# deletion entry shows the removal pass. Overrides use the model's bare
# field names -- the same names the inventory's ``users`` role mapping uses
# -- and win over it for every host.
USERS_OVERRIDES = {
    "groups_to_create": ({"name": "developers", "gid": 10000},),
    "users": (
        {
            "username": "reemote-demo",
            "name": "Reemote demo account",
            "groups": ("developers",),
            "append": True,
            "profile": "export REEMOTE_DEMO=1",
            "ssh_key": (
                (
                    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI0000000000000000"
                    "000000000000000000000000 reemote-demo@example.com"
                ),
            ),
            "generate_ssh_key": True,
        },
    ),
    "deleted": ({"username": "reemote-gone", "remove": True},),
}


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Resolve the role's variables before any host is contacted: the
        # inventory's users role mapping, overlaid with the call-site
        # overrides. A rejected value raises here -- a pydantic
        # ValidationError, which is a ValueError -- not mid-run.
        vars_set = resolve_vars(UsersVars, "users", inventory, overrides=USERS_OVERRIDES)
        # The role's whole task list: one per-host generator, barriers and
        # all. A plain (non-async) factory, so it is called, not awaited.
        deploy = users(vars_set)

        # Dry preview. With no barrier on this configuration the pass plans
        # the entire single wave and applies nothing -- the honest shape of
        # a barrier-free role under run_waves (like git and nodejs).
        report, _hosts = await run_waves(inventory, deploy, dry=True)
        print("\n== Wave tree (dry preview) ==")
        print(report.tree())

        # Real run: the one wave, planned and applied in lockstep across
        # hosts over SSH -- groups, per-user groups, users and key pairs,
        # authorized keys, profile blocks, and the deletions last.
        report, _hosts = await run_waves(inventory, deploy)
        print("\n== Wave tree (apply phase) ==")
        print(report.tree())
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON.
        # Python's own message already names the file and the reason, so
        # print just that -- no host was contacted and there is no traceback
        # worth showing.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema. The
        # message names the offending entry by its list index, which is the
        # whole report -- and it fails before any host is materialized.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (ValueError, TypeError) as exc:
        # A rejected role variable: pydantic's ValidationError is a
        # ValueError, so a blank username, a non-sequence ``users`` value or
        # an unknown ``update_password`` fails here, while the inventory is
        # resolved, with nothing applied.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed
        # error whose message names the host and the failing command. Print
        # that message -- no traceback -- and exit non-zero so the failure
        # stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
