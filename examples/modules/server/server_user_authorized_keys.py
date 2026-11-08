# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: authorise SSH public keys in a user's ``authorized_keys`` file.

This is a *usage* example, not part of the library. It shows reemote5's
``server.user_authorized_keys`` operation, the pyinfra
``server.user_authorized_keys`` parity for SSH keys: the deploy makes sure a
user's ``~/.ssh`` directory (mode ``700``) and ``authorized_keys`` file
(mode ``600``) exist and are owned correctly, then appends the public keys
that are not already listed -- pyinfra's documented flow, minus its
controller-side key-file paths (every entry here is a literal key string).

The operation is idempotent and reads three facts. The ``Home`` fact resolves
the directory when no ``authorized_key_directory`` is passed, a private
``_KeysPathStat`` probe compares the directory and the file against the
shared ``files.Directory``/``files.File`` state, and the ``AuthorizedKeys``
fact (read with the explicit key-file path) lists the keys already present --
only the missing ones ride an append. Two details are worth seeing in the
plan:

* creating a key file from nothing yields the whole setup -- ``mkdir -p`` +
  ``chmod 700`` + ``chown`` for ``~/.ssh``, then ``touch`` + ``chmod 600`` +
  ``chown`` for ``authorized_keys`` -- before the keys are appended, while a
  run where everything already matches yields nothing at all;
* the append command wraps its ``>>`` redirect in ``sh -c`` so the open
  happens *inside* the shell the driver elevates; a bare
  ``sudo echo ... >> <path>`` would open the redirect as the login user and
  fail on another user's ``600`` file.

``public_keys`` is a tuple: every ``op()`` argument must stay hashable for
``compose(dedupe=True)``, so a list would be rejected with
``UnhashableArgumentError``. Here the keys are managed for the login user
themselves, so no elevation is needed -- writing into ``~/.ssh`` under your
own home is unprivileged. Managing keys for *another* user is the mirror
case: flag the request ``sudo=True``, since the directory and file sit in
that user's home. pyinfra's ``delete_keys`` option is deliberately dropped --
that branch rewrites the whole file through an elevated ``files.put``, which
reemote5 cannot elevate -- so this operation only ever *adds* keys; inspect
what is there with the ``AuthorizedKeys`` fact and prune it with a file
recipe of your own.

Unlike the pytest suite -- which injects the test framework's
``SimulatedHost`` via ``run``'s ``host_factory`` and never touches the
network -- this example uses the *default* factory (``Host.from_inventory``):
each plain ``Host`` lazily opens an asyncssh connection, reads the home
directory, the ``.ssh``/``authorized_keys`` stat and the existing keys for
real, and runs the setup and append commands where they differ.

Every failure mode of the run -- an unreachable host, a rejected elevation,
an ``OperationError`` for a path that exists as the wrong type (an
``authorized_keys`` that is a directory, say), or a command that exits
non-zero -- reaches ``main`` as a typed error whose message already names the
host, so ``main`` catches them and prints one line each instead of a
traceback.

Note that this script mutates the target user's ``~/.ssh/authorized_keys``
for real: the appended keys stay in place. Re-running it is a no-op, because
the ``AuthorizedKeys`` fact already reports them.

Run it with::

    uv run python examples/modules/server/server_user_authorized_keys.py
"""

import asyncio
import sys
from collections.abc import Generator

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    OperationError,
    OperationRequest,
    PrivilegeError,
    format_log,
    format_plan,
    op,
    run,
    server,
)

# The inventory: real hosts and their SSH connection information, supplied
# by the application to the library. The default host_factory materializes
# each entry into a plain Host that connects over asyncssh.
INVENTORY = [
    {
        "host": "web1",
        "connection": {
            "address": "192.168.0.240",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
    {
        "host": "web2",
        "connection": {
            "address": "192.168.0.242",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
]

# The public keys to authorise, as literal key strings (never file paths --
# pyinfra's controller-side ``os.path.exists`` branch is deliberately
# dropped). A tuple, because ``op()`` arguments must stay hashable.
DESKTOP_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDemoDesktopKeyMaterial00 desktop@home"
LAPTOP_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDemoLaptopKeyMaterial0000 laptop@cafe"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Authorise the demo keys in the login user's authorized_keys file.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases; whether the ``.ssh`` directory, the key file and each key
    already exist is decided inside the operation against the freshly
    gathered facts, per phase.
    """
    # First request: create ``~/.ssh`` (mode 700) and ``authorized_keys``
    # (mode 600) if they are missing, then append both keys. For the login
    # user's own home no elevation is needed; managing another user's keys
    # would carry ``sudo=True`` instead.
    yield op(
        server.user_authorized_keys,
        name="Authorise the desktop and laptop keys",
        user="kjarvis",
        public_keys=(DESKTOP_KEY, LAPTOP_KEY),
    )
    # Second, identical request demonstrates the per-phase fact cache: in the
    # *plan* both requests read the same pre-deployment state, so both show a
    # change; in the *apply* each step re-reads the facts, so this step sees
    # the keys the first step wrote and yields nothing -- a no-op.
    yield op(
        server.user_authorized_keys,
        name="Re-authorise the same keys (idempotence check)",
        user="kjarvis",
        public_keys=(DESKTOP_KEY, LAPTOP_KEY),
    )


async def main() -> None:
    try:
        # --dry: prepare phase only. The home directory, the ``.ssh``/
        # ``authorized_keys`` stat and the existing keys are still gathered
        # over SSH (read-only) so the plan reflects each host's real
        # pre-deployment state; nothing is created and no key is appended.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts. Each step
        # re-reads the facts, so a second run of this script plans no change
        # anywhere.
        plans, report, _hosts = await run(INVENTORY, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except HostConnectionError as exc:
        # A host could not be reached over SSH at all. Report it plainly --
        # naming every unreachable host -- instead of dumping an asyncssh
        # traceback, and exit non-zero so the failure is script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except OperationError as exc:
        # The ``.ssh`` path or the key file already exists as the wrong type
        # (an ``authorized_keys`` that is a directory, say). The prepare phase
        # aborted before anything was sent; the message names host and path.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except PrivilegeError as exc:
        # The host was reached but rejected the elevation. One line naming the
        # host; the detail stays on ``__cause__``. (Not requested here -- the
        # login user's own keys are unprivileged -- but kept for the
        # another-user ``sudo=True`` variant.)
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except CommandError as exc:
        # A setup or append command exited non-zero. The typed message already
        # names the host, the credential-free command and its stderr, so print
        # just that.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
