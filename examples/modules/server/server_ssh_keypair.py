# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: generate a user's SSH key pair, idempotently.

This is a *usage* example, not part of the library. It shows reemote's
``server.SshKeyPair`` fact and ``server.ssh_keypair`` operation, the
``user`` module's ``generate_ssh_key`` parity: the deploy gives a user an
``~/.ssh`` directory (mode ``700``, owned by them) and one key pair inside
it, and does nothing at all on the next run because the public half already
has a fingerprint.

The operation reads three facts. The ``Home`` fact resolves the directory
when no ``path`` is passed -- an unknown user raises ``OperationError``
rather than planning a literal ``None/.ssh`` mkdir -- a private
``_KeysPathStat`` probe compares the ``.ssh`` directory against the shared
``files.Directory`` state, and the ``SshKeyPair`` fact runs ``ssh-keygen -lf``
for the public half. That last read is two-tier: a key inside another user's
``700`` directory is unreadable as the login user, so the fact retries it
elevated and only then reports a fingerprint. Three details are worth seeing
in the plan:

* a user with no ``.ssh`` yet gets the whole setup -- ``mkdir -p`` +
  ``chmod 700`` + ``chown`` -- before the key pair, while a run where the
  pair already exists yields nothing at all;
* the generation is one ``ssh-keygen`` command, wrapped in ``sh -c`` so one
  ``sudo`` elevates the whole chain, and run as the target user through
  ``su -s /bin/sh <user>``: the pair is born owned by them at mode ``600``
  for the private half and ``644`` for the public one, so the key files need
  no follow-up state verbs of their own;
* a passphrase is refused rather than spooled: ``passphrase=...`` raises
  ``ValueError`` while the request materializes -- before a fact is read or a
  command sent -- because no secret may ride a plan, a command log or a
  ``/tmp`` file on the target (the ``gpg.gen_key`` ``%no-protection``
  precedent). Every pair this example generates is unprotected, commented
  with who and where it belongs to.

Elevation is not optional here, even for the login user's own home: the
keygen is run through ``su`` so the files land owned correctly, and only root
may ``su`` without a password, so every request carries ``sudo=True``. The key
type changes the default file name (``id_<type>``) and only ``rsa``/``rsa1``
carry the ``size`` as ``-b`` -- ``ed25519`` has no length at all, which is why
the plan below has no ``-b`` in it. Every ``op()`` argument must stay hashable
for ``compose(dedupe=True)``, which is why the values here are strings and
numbers rather than any container.

Unlike the pytest suite -- which injects the test framework's
``SimulatedHost`` via ``run``'s ``host_factory`` and never touches the
network -- this example uses the *default* factory (``Host.from_inventory``):
each plain ``Host`` lazily opens an asyncssh connection, reads the home
directory, the ``.ssh`` stat and the existing fingerprint for real, and runs
the setup and generation commands where they differ.

Every failure mode of the run -- an unreachable host, a rejected elevation, an
``OperationError`` for a user the host does not know (no home directory to
resolve) or a path that already exists as the wrong type (an ``.ssh`` that is a
regular file), or a command that exits non-zero -- reaches ``main`` as a typed
error whose message already names the host, so ``main`` catches them and prints
one line each instead of a traceback.

Note that this script creates real key pairs in the target user's ``~/.ssh``:
the files stay in place. Re-running it is a no-op, because the ``SshKeyPair``
fact already reports a fingerprint for each public half -- only ``force=True``
would regenerate a pair, and this example never asks for that.

Every request carries ``when=is_centos``, the declarative eligibility guard:
the predicate reads only **static** inventory data -- ``host.group``, set by
the ``"group"`` key of the inventory entry -- and is a stable module-level
function, so the prepare and apply phases reach the same skip decision. The
driver skips a rejected request without materializing it, so on hosts outside
the ``"centos"`` group neither the commands nor the facts behind them run, and
plan and log render the step as ``[skipped: when guard is_centos]`` in its
lockstep slot. The same guard is applied by hand to the standalone
``gather_fact`` read at the top of ``main``, which has no ``when`` parameter of
its own.

The path is resolved from ``__file__``, so the example works from any working
directory; point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/server/server_ssh_keypair.py
"""

import asyncio
import getpass
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
    OperationError,
    OperationRequest,
    PrivilegeError,
    format_log,
    format_plan,
    gather_fact,
    op,
    run,
    server,
    validate_inventory,
)

# examples/modules/server/server_ssh_keypair.py -> the project root, so the
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


def login_user(host: Host) -> str:
    """The user a host is logged in as, from its static ``connection`` data.

    The managed account is the login user themselves: the ``username`` key of
    the inventory entry, or its ``user`` alias. With neither in the connection
    asyncssh falls back to the local user, so this does too. A hard-coded name
    would not exist on hosts that log in as somebody else, and the operation
    rejects an unknown user. Static data only -- the same answer in both deploy
    phases.
    """
    return host.connection.get("username") or host.connection.get("user") or getpass.getuser()


def is_centos(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"centos"``.

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision.
    """
    return host.group == "centos"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Give the login user two SSH key pairs in their own ``~/.ssh``.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases; whether the directory and each pair already exist is
    decided inside the operation against the freshly gathered facts, per
    phase.
    """
    user = login_user(host)
    # First request: an unprotected ed25519 pair at the default
    # ``~/.ssh/id_ed25519``, commented with who and where it belongs to. sudo
    # is required even for the login user's own home, because the keygen runs
    # as that user through su.
    yield op(
        server.ssh_keypair,
        name="Generate the ed25519 login key",
        user=user,
        type="ed25519",
        comment=f"{user}@{host.name}",
        sudo=True,
        when=is_centos,
    )
    # Second, identical request demonstrates the per-phase fact cache: in the
    # *plan* both requests read the same pre-deployment state, so both show a
    # change; in the *apply* each step re-reads the facts, so this step sees
    # the pair the first step generated and yields nothing -- a no-op.
    yield op(
        server.ssh_keypair,
        name="Re-generate the same key (idempotence check)",
        user=user,
        type="ed25519",
        comment=f"{user}@{host.name}",
        sudo=True,
        when=is_centos,
    )
    # Third request: the RSA variant at the default ``~/.ssh/id_rsa`` with the
    # conventional 4096 bits, commented with where it lives. Where an
    # ``id_rsa`` already exists this is a no-op -- the public half's
    # fingerprint is the state the operation compares against. No passphrase is
    # passed: a non-empty one raises ``ValueError`` at materialization, because
    # passphrase-protected generation is out of scope.
    yield op(
        server.ssh_keypair,
        name="Generate an RSA key pair",
        user=user,
        type="rsa",
        size=4096,
        comment=f"{user}@{host.name}-rsa",
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    gathered: list[Host] = []
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Materialize every entry up front, then guard the standalone read the
        # way the driver guards a request: gather_fact takes no ``when``, so
        # filtering the host list is the equivalent, and a skipped host never
        # gets connected.
        all_hosts = [Host.from_inventory(entry) for entry in inventory]
        gathered = [host for host in all_hosts if is_centos(host)]
        skipped = [host.name for host in all_hosts if host not in gathered]
        if skipped:
            print("\n== Skipped (when guard is_centos): " + ", ".join(skipped) + " ==")
        # The fact on its own, before anything is deployed: the driver fans
        # SshKeyPair.process out across the guarded hosts concurrently and
        # returns a host-name-keyed mapping. Each read is ``ssh-keygen -lf`` on
        # the conventional ``~/.ssh/id_rsa.pub``, retried elevated when the
        # login user cannot read the file -- None means there is no pair.
        if gathered:
            key_pairs = await gather_fact(gathered, server.SshKeyPair, login_user(gathered[0]))
            print("\n== Existing key pairs (server.SshKeyPair) ==")
            for host in gathered:
                print(f"{host.name}: {key_pairs[host.name] or 'no ~/.ssh/id_rsa.pub'}")

        # --dry: prepare phase only. The home directory, the ``.ssh`` stat and
        # the fingerprints are still gathered over SSH (read-only) so the plan
        # reflects each host's real pre-deployment state; nothing is created
        # and no key is generated.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("\n== Plan (prepare phase, --dry) ==")
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
        # The managed user does not exist on the host (no home directory to
        # resolve), or the ``.ssh`` path already exists as the wrong type (an
        # ``.ssh`` that is a regular file, say). The prepare phase aborted
        # before anything was sent; the message names the host and the user or
        # path.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except PrivilegeError as exc:
        # The host was reached but rejected the elevation -- which this deploy
        # always asks for, since the keygen runs as the target user. One line
        # naming the host; the detail stays on ``__cause__``.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except CommandError as exc:
        # A setup or generation command exited non-zero -- no ``ssh-keygen`` on
        # the host, or an unwritable home. The typed message already names the
        # host, the credential-free command and its stderr, so print just that.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection the standalone gather opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in gathered))


if __name__ == "__main__":
    asyncio.run(main())
