# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: install httpd and remove its welcome page using ``doas``.

This is a *usage* example, not part of the library. It is the ``doas``
counterpart of ``privilege_su.py`` -- same deploy shape, same two-phase ``run``
flow over the real asyncssh transport -- but the operations ask to elevate with
``doas=True`` instead of ``su=True``.

doas is passwordless here. ``doas`` (OpenBSD's ``doas``; ``opendoas`` on Linux)
has no ``-A`` askpass mechanism like sudo and reads a password only from a TTY,
which asyncssh's non-interactive channels do not provide. reemote therefore
always runs ``doas -n`` -- *non-interactive*, so it fails rather than prompts --
and there is deliberately no ``doas_password`` inventory option. The host must
grant the login user a ``permit nopass`` rule in ``/etc/doas.conf``; without one
``doas -n`` is denied and reemote reports a ``PrivilegeError``. In this respect
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

The hosts come from the shared ``inventory.json`` in the project root, read by
``load_inventory`` and validated. ``is_centos`` (``host.group == "centos"``) is
composed onto the deploy with ``sub_deploy(..., when=is_centos)``, so the
``dnf`` work runs only on the ``centos`` hosts and is skipped -- facts included
-- on the rest; the inner operations carry no ``when`` of their own. The
``privilege`` section this demo needs is not in the shared file, so ``main``
attaches it to the loaded ``centos`` entries: privilege stays an *inventory*
parameter, never a deploy-script one.

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
# entries (the shared file carries none). ``doas_user`` is the ``doas`` target
# -- ``root`` here, because installing packages needs superuser. ``root`` is
# ``doas``'s own default target, so the command sent is a bare
# ``doas -n dnf install ...`` with no explicit user. There is NO password: doas
# is passwordless in reemote, so the host must grant the login user a
# ``permit nopass`` rule in ``/etc/doas.conf`` -- e.g. ``permit nopass kim``.
# The deploy script supplies neither the user nor a credential -- it only asks
# for ``doas=True``.
PRIVILEGE = {"doas_user": "root"}


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
    phases, and both operations are flagged ``doas=True``. Neither names a target
    user -- the inventory's ``privilege`` section does. Neither carries a
    ``when``: the ``is_centos`` group guard is composed onto the whole deploy
    below, so it need not be repeated on the inner operations.
    """
    yield op(
        dnf.packages, name="Install httpd", packages=("httpd",), present=True, doas=True
    )
    yield op(
        files.file,
        name="Remove welcome configuration",
        path=HTTPD_WELCOME_CONF,
        present=False,
        doas=True,
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

        # Real run: prepare, then apply over SSH, running each ``doas=True``
        # command as the inventory's doas target via passwordless ``doas -n``.
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
