"""Example: install httpd and remove its welcome page using a sudo password.

This is a *usage* example, not part of the library. It builds on
``dnf_packages.py`` -- same deploy, same two-phase ``run`` flow over the real
asyncssh transport -- and adds the one thing that example assumes away: a
sudo **password**.

The difference lives entirely in the inventory. An entry may carry a
``privilege`` section, and here it supplies the run-as user and the password:

- The deploy script is unchanged: every operation still only *asks* to
  elevate with ``sudo=True``. It never names a user or a password -- those
  are privilege parameters, and letting a deploy script choose them is a
  security risk, so they come from the inventory alone.
- With a ``sudo_password`` configured, the real transport elevates through
  ``sudo -A`` against a small askpass helper it generates on the host, so the
  password is supplied without a TTY (asyncssh channels are non-interactive).
  Without one, elevation would assume passwordless sudo.

The password is plain text in the inventory -- the only supported form -- and
is shell-quoted into the command sent over the encrypted channel. It never
reaches the rendered log: ``format_log`` shows the credential-free
``$ sudo dnf install -y httpd``, because the log renders command descriptors
through ``command_shell``, which is deliberately password-free.

A wrong password is reported once, not three times. reemote5 invokes ``sudo -A``
exactly once, but sudo re-runs the askpass helper up to its own ``passwd_tries``
limit (3 by default), so one bad credential costs three identical attempts plus
sudo's ``Sorry, try again.`` noise. ``Host.run_shell_sudo`` recognises that
output as sudo's *own* authentication rejection and raises ``PrivilegeError`` --
the privilege counterpart of ``HostConnectionError`` -- whose message names the
affected host(s) and nothing else, keeping the exit status and sudo's stderr as
``__cause__`` for diagnosis. ``main`` below catches it and prints one line, so a
rejected credential is reported plainly instead of as a traceback. A command
that fails *after* sudo has authenticated is still the ordinary
``CommandError``: it also aborts the run, and ``main`` reports it the same way
-- one line, no traceback.

Run it with::

    uv run python examples/demonstrations/privilege/privilege_sudo_pw.py
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

# A single real host. ``connection.username`` (kjarvis) is the login user whose
# ``sudo_password`` authenticates elevation; ``privilege.sudo_user`` is the
# run-AS target -- ``root`` here, because installing packages needs superuser
# (``sudo -u kjarvis dnf install`` fails: kjarvis is not root). ``root`` is
# ``sudo``'s own default target, so the command sent is a bare ``sudo dnf
# install ...`` with no ``-u root``. The deploy script supplies neither user nor
# password -- it only asks for ``sudo=True``.
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


def deploy(host: Host) -> Generator[OperationRequest]:
    """Install httpd, then remove its default welcome configuration.

    Unchanged from ``dnf_packages.py``: branch-free, so the request sequence is
    identical in the prepare and apply phases, and both operations are flagged
    ``sudo=True``. Neither names a run-as user or a password -- the inventory's
    ``privilege`` section does.
    """
    yield op(dnf.packages, name="Install httpd", packages=("httpd",),
             present=False,
             sudo=True)
    yield op(
        files.file,
        name="Remove welcome configuration",
        src=HTTPD_WELCOME_CONF,
        present=False,
        sudo=True,
    )


async def main() -> None:
    try:
        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects the host's real pre-deployment state; no command
        # that mutates the host is sent.
        plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply over SSH, elevating each ``sudo=True``
        # command with the inventory's sudo password via the askpass helper.
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
        # ``sudo_password``, or a run-as user the login user may not become.
        # One line naming the host rather than sudo's three "Sorry, try again."
        # retries; the exit status and stderr stay on ``exc.__cause__``.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except CommandError as exc:
        # sudo authenticated, but the command it ran exited non-zero. The typed
        # message already names the host, the credential-free command and its
        # stderr, so print just that one report -- no traceback.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
