# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: install httpd and remove its welcome page using ``doas``.

This is a *usage* example, not part of the library. It is the ``doas``
counterpart of ``privilege_su.py`` -- same deploy shape, same two-phase ``run``
flow over the real asyncssh transport -- but the operations ask to elevate with
``doas=True`` instead of ``su=True``.

doas is passwordless here. ``doas`` (OpenBSD's ``doas``; ``opendoas`` on Linux)
has no ``-A`` askpass mechanism like sudo and reads a password only from a TTY,
which asyncssh's non-interactive channels do not provide. reemote5 therefore
always runs ``doas -n`` -- *non-interactive*, so it fails rather than prompts --
and there is deliberately no ``doas_password`` inventory option. The host must
grant the login user a ``permit nopass`` rule in ``/etc/doas.conf``; without one
``doas -n`` is denied and reemote5 reports a ``PrivilegeError``. In this respect
doas is simpler than both sudo and su: there is no credential to supply, and so
none that could leak into a log.

As with sudo and su, the deploy script never names a user -- it only asks for
``doas=True`` -- so the run-as target comes from the inventory ``privilege``
section alone:

- ``doas_user`` is the target of ``doas -n -u <user>``. Since ``doas`` defaults
  to ``root``, a ``doas_user`` of ``root`` (or none at all) collapses to a bare
  ``doas -n '<command>'``.

Combining escalations. An operation may be flagged ``doas=True`` together with
``sudo=True`` and/or ``su=True``; they are then applied in a fixed order --
``doas`` then ``sudo`` then ``su`` -- so ``doas`` is the *outermost* escalation
and ``su`` the innermost (``doas -n ... sudo ... su <user> -c ...``). This
example uses ``doas`` alone.

The rendered log shows the credential-free ``$ doas -n dnf install -y httpd``:
``format_log`` renders command descriptors through ``command_shell``, which never
carries a credential -- and for doas there is none to carry anyway.

A denial is reported once, plainly. ``Host.run_shell_doas`` recognises doas's
*own* authorization/authentication rejection (``doas: Operation not permitted``,
``doas: Authentication required``, ``doas: Authentication failed``) and raises
``PrivilegeError`` -- the privilege counterpart of ``HostConnectionError`` --
whose message names the affected host(s) and nothing else, keeping the exit
status and doas's stderr as ``__cause__`` for diagnosis. ``main`` below catches
it and prints one line. A command that fails *after* doas has authorized is still
the ordinary ``CommandError``: it also aborts the run, and ``main`` reports it the
same way -- one line, no traceback.

Run it with::

    uv run python examples/demonstrations/privilege/privilege_doas.py
"""

import asyncio
import sys
from collections.abc import Generator

from reemote5 import (
    CommandError,
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
# ``privilege.doas_user`` is the ``doas`` target -- ``root`` here, because
# installing packages needs superuser. ``root`` is ``doas``'s own default target,
# so the command sent is a bare ``doas -n dnf install ...`` with no explicit
# user. There is NO password: doas is passwordless in reemote5, so the host must
# grant kjarvis a ``permit nopass`` rule in ``/etc/doas.conf`` -- e.g.
# ``permit nopass kjarvis``. The deploy script supplies neither the user nor a
# credential -- it only asks for ``doas=True``.
INVENTORY = [
    {
        "host": "centos9-doas",
        "connection": {
            "address": "192.168.0.141",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
        "privilege": {
            "doas_user": "root",
        },
    },
]


def deploy(host: Host) -> Generator[OperationRequest]:
    """Install httpd, then remove its default welcome configuration.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases, and both operations are flagged ``doas=True``. Neither names a target
    user -- the inventory's ``privilege`` section does.
    """
    yield op(dnf.packages, name="Install httpd", packages=("httpd",),
             present=True,
             doas=True)
    yield op(
        files.file,
        name="Remove welcome configuration",
        src=HTTPD_WELCOME_CONF,
        present=False,
        doas=True,
    )


async def main() -> None:
    try:
        # --dry: prepare phase only. Facts are still gathered over SSH
        # (read-only) so the plan reflects the host's real pre-deployment state;
        # no command that mutates the host is sent.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply over SSH, running each ``doas=True``
        # command as the inventory's doas target via passwordless ``doas -n``.
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
        # The host was reached but doas denied the escalation -- no matching
        # ``permit nopass`` rule for the login user, or a forbidden run-as
        # target. One line naming the host; the exit status and doas's stderr
        # stay on ``exc.__cause__``.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except CommandError as exc:
        # doas authorized, but the command it ran exited non-zero. The typed
        # message already names the host, the credential-free command and its
        # stderr, so print just that one report -- no traceback.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
