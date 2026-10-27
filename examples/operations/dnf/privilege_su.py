"""Example: install httpd and remove its welcome page using ``su``.

This is a *usage* example, not part of the library. It is the ``su``
counterpart of ``privilege_sudo_pw.py`` -- same deploy shape, same two-phase
``run`` flow over the real asyncssh transport -- but the operations ask to
elevate with ``su=True`` instead of ``sudo=True``.

Only *default* ``su`` is supported: ``su <user> -c '<command>'``, with no login
shell (``su -l``), no environment preservation and no shell override. As with
sudo, the deploy script never names a user or a password -- it only asks for
``su=True`` -- so the run-as target and the credential come from the inventory
``privilege`` section alone:

- ``su_user`` is the target of ``su <user> -c``. Since ``su`` defaults to
  ``root``, a ``su_user`` of ``root`` (or none at all) collapses to a bare
  ``su -c '<command>'``.
- ``su_password`` supplies the target's password. ``su`` has no ``-A`` askpass
  mechanism like sudo, so reemote5 generates a small helper on the host that
  prints the password and pipes it to su's stdin
  (``env REEMOTE5_SU_PASSWORD=... <helper> | su ...``), which works over
  asyncssh's non-interactive channels. Without a password, plain ``su`` is
  used (which assumes the login user may already become the target).

Combining escalations. An operation may be flagged both ``sudo=True`` and
``su=True``; they are then applied in a fixed order -- ``sudo`` then ``su`` --
so ``sudo`` is the outer escalation and runs ``su`` inside it
(``sudo ... su <user> -c ...``). This example uses ``su`` alone.

The password is plain text in the inventory -- the only supported form -- and
is shell-quoted into the command sent over the encrypted channel. It never
reaches the rendered log: ``format_log`` shows the credential-free
``$ su <user> -c 'dnf install -y httpd'``, because the log renders command
descriptors through ``command_shell``, which is deliberately password-free.

A wrong password is reported once, plainly. ``Host.run_shell_su`` recognises
su's *own* authentication rejection (``su: Authentication failure``) and raises
``PrivilegeError`` -- the privilege counterpart of ``HostConnectionError`` --
whose message names the affected host(s) and nothing else, keeping the exit
status and su's stderr as ``__cause__`` for diagnosis. ``main`` below catches it
and prints one line. A command that fails *after* su has authenticated is still
the ordinary ``CommandError``, and still aborts the run.

Run it with::

    uv run python examples/operations/dnf/privilege_su.py
"""

import asyncio
import sys
from collections.abc import Generator

from reemote5 import (
    Host,
    HostConnectionError,
    OperationRequest,
    PrivilegeError,
    dnf,
    files,
    format_log,
    format_plan,
    op,
    run,
)

HTTPD_WELCOME_CONF = "/etc/httpd/conf.d/welcome.conf"

# A single real host. ``connection.username`` (kjarvis) is the login user;
# ``privilege.su_user`` is the ``su`` target -- ``root`` here, because
# installing packages needs superuser. ``root`` is ``su``'s own default target,
# so the command sent is a bare ``su -c 'dnf install ...'`` with no explicit
# user. ``su_password`` is the root password, piped to su's stdin through the
# generated helper. The deploy script supplies neither the user nor the
# password -- it only asks for ``su=True``.
INVENTORY = [
    {
        "host": "centos9-su",
        "connection": {
            "address": "192.168.0.140",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
        "privilege": {
            "su_user": "root",
            "su_password": "qsefth*4",
        },
    },
]


def deploy(host: Host) -> Generator[OperationRequest]:
    """Install httpd, then remove its default welcome configuration.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases, and both operations are flagged ``su=True``. Neither names a target
    user or a password -- the inventory's ``privilege`` section does.
    """
    yield op(dnf.packages, name="Install httpd", packages=("httpd",),
             present=False,
             su=True)
    yield op(
        files.file,
        name="Remove welcome configuration",
        src=HTTPD_WELCOME_CONF,
        present=False,
        su=True,
    )


async def main() -> None:
    try:
        # --dry: prepare phase only. Facts are still gathered over SSH
        # (read-only) so the plan reflects the host's real pre-deployment state;
        # no command that mutates the host is sent.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply over SSH, running each ``su=True``
        # command as the inventory's su target with its password piped to su's
        # stdin via the askpass helper.
        plans, report, _hosts = await run(INVENTORY, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except HostConnectionError as exc:
        # The host could not be reached over SSH at all. Report it plainly --
        # naming every unreachable host -- instead of dumping an asyncssh
        # traceback, and exit non-zero so the failure is script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except PrivilegeError as exc:
        # The host was reached but rejected the configured privilege -- a wrong
        # ``su_password``, or a target user the login user may not become. One
        # line naming the host; the exit status and stderr stay on
        # ``exc.__cause__``.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
