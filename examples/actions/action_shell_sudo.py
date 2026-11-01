"""Example: run an elevated bespoke command with a sudo password.

This is a *usage* example, not part of the library. It is the action analogue
of ``examples/demonstrations/privilege/privilege_sudo_pw.py``: same inventory
``privilege`` section supplying the run-as user and password, but driving
``action.Shell`` through ``gather_action`` instead of a two-phase deploy.

The elevation contract is identical to an operation's:

- The call only *asks* to elevate with ``sudo=True``. It never names a user or
  a password -- those are privilege parameters and come from the inventory's
  ``privilege`` section alone (``sudo_user``, ``sudo_password``). Letting a
  caller choose them would be a security risk.
- With a ``sudo_password`` configured, the transport elevates through
  ``sudo -A`` against a small askpass helper it generates on the host, so the
  password is supplied without a TTY (asyncssh channels are non-interactive).
  Without one, elevation assumes passwordless sudo.

The script runs ``id`` twice on the single host: once unprivileged (reporting
the login user) and once with ``sudo=True`` (reporting root). Both come back as
``Result`` objects; the elevated one rides the raw-result privileged path
(``run_shell_privileged_result``), so the command's own exit status is data --
but a *rejected credential* is different. If sudo refuses the password, that is
sudo's OWN authentication failure, not the bespoke command's outcome, and it
raises ``PrivilegeError`` (the privilege counterpart of ``HostConnectionError``)
rather than travelling back as an exit status. ``main`` catches it and prints
one line naming the host, keeping the exit status and stderr as ``__cause__``.

The password is plain text in the inventory -- the only supported form -- and
never reaches the ``Result`` or any log: it lives only in the execution string
sent over the encrypted channel. Each ``gather_action`` call re-connects and
closes the host (removing the askpass helper) when it finishes.

Run it with::

    uv run python examples/actions/action_shell_sudo.py
"""

import asyncio
import sys

from reemote5 import (
    Host,
    HostConnectionError,
    PrivilegeError,
    Shell,
    gather_action,
)

# A single real host. ``connection.username`` (kjarvis) is the login user whose
# ``sudo_password`` authenticates elevation; ``privilege.sudo_user`` is the
# run-AS target -- ``root`` here, because only root may read /etc/shadow.
# ``root`` is sudo's own default target, so the command sent is a bare
# ``sudo id`` with no ``-u root``. The call supplies neither user nor password.
INVENTORY = [
    {
        "host": "centos9-sudo-pw",
        "connection": {
            "address": "192.168.0.140",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
        "privilege": {
            "sudo_user": "root",
            "sudo_password": "qsefth*4",
        },
    },
]


async def main() -> None:
    hosts = [Host.from_inventory(entry) for entry in INVENTORY]
    try:
        # Unprivileged: id reports the login user (kjarvis).
        plain = await gather_action(hosts, Shell, command="id")

        # Elevated: sudo=True asks for elevation; the inventory's sudo_user
        # and sudo_password supply the target and credential via askpass. id
        # now reports root. The Result carries the command's own outcome.
        elevated = await gather_action(hosts, Shell, command="id", sudo=True)
    except HostConnectionError as exc:
        # The host could not be reached over SSH at all.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except PrivilegeError as exc:
        # The host was reached but rejected the configured sudo credential.
        # One line naming the host -- not sudo's three "Sorry, try again."
        # retries; the exit status and stderr stay on exc.__cause__.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    for label, results in (("id (unprivileged)", plain), ("id (sudo=True)", elevated)):
        print(f"== {label} ==")
        for name, result in results.items():
            print(f"{name}: exit={result.exit_status} ok={result.ok}")
            print(f"  {result.stdout.strip()}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
