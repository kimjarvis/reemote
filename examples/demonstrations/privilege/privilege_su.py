# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

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
  mechanism like sudo, so reemote generates a small helper on the host that
  prints the password and pipes it to su's stdin
  (``env REEMOTE_SU_PASSWORD=... <helper> | su ...``), which works over
  asyncssh's non-interactive channels. Without a password, plain ``su`` is
  used (which assumes the login user may already become the target).

Combining escalations. An operation may be flagged both ``sudo=True`` and
``su=True``; they are then applied in a fixed order -- ``sudo`` then ``su`` --
so ``sudo`` is the outer escalation and runs ``su`` inside it
(``sudo ... su <user> -c ...``). This example uses ``su`` alone.

The hosts come from the shared ``inventory.json`` in the project root, read by
``load_inventory`` and validated. ``is_centos`` (``host.group == "centos"``) is
composed onto the deploy with ``sub_deploy(..., when=is_centos)``, so the
``dnf`` work runs only on the ``centos`` hosts and is skipped -- facts included
-- on the rest; the inner operations carry no ``when`` of their own. The
``privilege`` section this demo needs is not in the shared file, so ``main``
attaches it to the loaded ``centos`` entries: privilege stays an *inventory*
parameter, never a deploy-script one.

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
the ordinary ``CommandError``: it also aborts the run, and ``main`` reports it
the same way -- one line, no traceback.

Run it with::

    uv run python examples/demonstrations/privilege/privilege_su.py
"""

import asyncio
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
    OperationRequest,
    PrivilegeError,
    dnf,
    files,
    format_log,
    format_plan,
    op,
    run,
    sub_deploy,
    validate_inventory,
)

HTTPD_WELCOME_CONF = "/etc/httpd/conf.d/welcome.conf"

# The shared inventory in the project root -- the same inventory.json the
# library's other examples read.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The privilege section this demo attaches to the loaded ``centos`` inventory
# entries (the shared file carries none). ``su_user`` is the ``su`` target --
# ``root`` here, because installing packages needs superuser. ``root`` is
# ``su``'s own default target, so the command sent is a bare
# ``su -c 'dnf install ...'`` with no explicit user. ``su_password`` is the
# root password, piped to su's stdin through the generated helper. The deploy
# script supplies neither the user nor the password -- it only asks for
# ``su=True``.
PRIVILEGE = {"su_user": "root", "su_password": "qsefth*4"}


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    ``json.loads`` parses the file into a list of entry dicts, and
    ``validate_inventory`` checks every entry against the inventory schema --
    a malformed file raises before any connection is attempted.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_centos(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"centos"``.

    Reads only the host's static ``group`` field, so it is deterministic across
    the prepare and apply phases.
    """
    return host.group == "centos"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Install httpd, then remove its default welcome configuration.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases, and both operations are flagged ``su=True``. Neither names a target
    user or a password -- the inventory's ``privilege`` section does. Neither
    carries a ``when``: the ``is_centos`` group guard is composed onto the whole
    deploy below, so it need not be repeated on the inner operations.
    """
    yield op(
        dnf.packages, name="Install httpd", packages=("httpd",), present=True, su=True
    )
    yield op(
        files.file,
        name="Remove welcome configuration",
        path=HTTPD_WELCOME_CONF,
        present=False,
        su=True,
    )


# The group guard, composed once at the outermost level: every request above
# runs only on a ``centos`` host, and a skipped request is never materialized,
# so its facts (``DnfPackages``, the welcome file's ``FilesInfo``) are never
# read on the other hosts either.
deploy = sub_deploy(deploy, when=is_centos)


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file itself is missing or not valid JSON.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The file parsed, but an entry violates the inventory schema.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    # The shared inventory carries no privilege section; attach this demo's to
    # the centos entries, so elevation stays an inventory parameter and the
    # credential is scoped to the group that actually elevates.
    for entry in inventory:
        if entry.get("group") == "centos":
            entry["privilege"] = dict(PRIVILEGE)

    print(f"== Inventory loaded from {INVENTORY_PATH} ==")
    try:
        # --dry: prepare phase only. Facts are still gathered over SSH
        # (read-only) so the plan reflects the host's real pre-deployment state;
        # no command that mutates the host is sent.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply over SSH, running each ``su=True``
        # command as the inventory's su target with its password piped to su's
        # stdin via the askpass helper.
        plans, report, _hosts = await run(inventory, deploy)
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
    except CommandError as exc:
        # su authenticated, but the command it ran exited non-zero. The typed
        # message already names the host, the credential-free command and its
        # stderr, so print just that one report -- no traceback.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
