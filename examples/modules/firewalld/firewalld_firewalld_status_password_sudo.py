# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: read ``firewalld.FirewalldStatus`` on a host that needs a sudo password.

This is a *usage* example, not part of the library. Where the plain fact
example (``examples/modules/firewalld/firewalld_firewalld_status.py``)
reads the daemon state over key-based SSH and lets the fact's own
privilege ladder stop at passwordless ``sudo -n``, this one shows the
*elevated* rung of that ladder actually carrying a credential: it
attaches an inventory ``privilege`` section (``sudo_user``/
``sudo_password``) to the RedHat entries before gathering the fact,
exactly as ``examples/actions/action_shell_sudo.py`` does for a bespoke
command.

The read is two-tier (the module's privilege note):

1. A cheap in-shell probe runs the marked ``firewall-cmd --state`` /
   ``--version`` shell as the login user, retrying under passwordless
   ``sudo -n`` on failure. On a host whose user has no passwordless sudo
   this whole probe is denied.
2. When the probe is *denied* -- recognised by an error signature such as
   "a password is required", never by emptiness -- the fact re-reads
   through ``host.run_shell_privileged(command, sudo=True)``, which
   elevates with the inventory's ``sudo_password`` over a generated
   askpass helper. This keeps a refusal from masquerading as
   ``running=False``.

The wrapper still distinguishes a genuine non-root answer from a refusal:
a *stopped* daemon (or a host without firewalld) is real state the
failure gate turns into a clean zero, so it reads ``running=False`` with
an empty ``version`` on either tier rather than elevating -- an empty
version beside a running status is how a deploy tells a firewalld host
apart from a bare one.

Privilege stays an *inventory* parameter: the ``gather_fact`` call names
neither user nor password, and the credential never reaches the parsed
fact or any log -- it lives only in the execution string sent over the
encrypted channel. If the host *rejects* the configured password, that is
sudo's own authentication failure and raises ``PrivilegeError`` (the
privilege counterpart of ``HostConnectionError``) rather than travelling
back as ``running=False``; ``main`` catches it and prints one line naming
the host. A host without ``firewall-cmd`` installed raises
``CommandNotFoundError`` instead.

The hosts come from ``inventory.json`` in the current working directory,
parsed as JSON and validated with ``validate_inventory``; ``is_redhat``
(``host.family == "RedHat"``) filters the host list before the
``gather_fact`` fan-out, and the ``privilege`` section is attached only to
those entries so the credential is scoped to the group that actually
elevates. Edit the shared file there to target your machines and put the
login user's real password in ``PRIVILEGE``.

Run it with::

    uv run python examples/modules/firewalld/firewalld_firewalld_status_password_sudo.py
"""

import asyncio
import json
import sys
from pathlib import Path

from reemote import (
    CommandError,
    FirewalldStatus,
    Host,
    HostConnectionError,
    InventoryError,
    PrivilegeError,
    gather_fact,
    validate_inventory,
)

# The privilege section this demo attaches to the loaded RedHat inventory
# entries (the shared file carries none). The login user's ``sudo_password``
# authenticates the elevated re-read of the daemon state; ``sudo_user`` is
# the run-AS target -- ``root`` here, matching the other firewalld reads.
# ``root`` is sudo's own default target, so the elevated command carries no
# ``-u root``. The gather_fact call itself supplies neither user nor
# password.
PRIVILEGE = {"sudo_user": "root", "sudo_password": "qsefth*4"}


def is_redhat(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"RedHat"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision.
    """
    return host.family == "RedHat"


async def main() -> None:
    hosts: list[Host] = []
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)

        # The shared inventory carries no privilege section; attach this
        # demo's to the RedHat entries before materialising them, so the
        # fact's elevated tier has a credential to re-read with. Privilege
        # stays an inventory parameter, scoped to the elevating group.
        for entry in inventory:
            if entry.get("family") == "RedHat":
                entry["privilege"] = dict(PRIVILEGE)

        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Materialize every inventory entry up front, then guard the gather
        # below -- a skipped host never reaches the fan-out at all.
        all_hosts = [Host.from_inventory(entry) for entry in inventory]
        hosts = [host for host in all_hosts if is_redhat(host)]
        skipped = [host.name for host in all_hosts if host not in hosts]
        if skipped:
            print("\n== Skipped (when guard is_redhat): " + ", ".join(skipped) + " ==")
        # Gather the fact across the guarded hosts concurrently. A login-user
        # denial falls through to run_shell_privileged, which elevates with
        # the sudo_password above -- so a refusal no longer reads as
        # running=False. (A genuinely stopped daemon still reads False.)
        statuses = await gather_fact(hosts, FirewalldStatus)
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError) as exc:
        # Unreachable host, or firewall-cmd not installed (a
        # CommandNotFoundError). Print the typed message -- no traceback.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except PrivilegeError as exc:
        # The host was reached but rejected the configured sudo credential,
        # so the elevated re-read failed. One line naming the host -- not
        # sudo's "Sorry, try again." retries; the exit status and stderr stay
        # on exc.__cause__.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        # Close every connection gather_fact may have opened, concurrently.
        await asyncio.gather(*(host.aclose() for host in hosts))
    # Print in inventory order (gather_fact keys the results by host name).
    for host in hosts:
        print(f"== {host.name}: firewalld.FirewalldStatus ==")
        print(json.dumps(statuses[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
