# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: read ``iptables.IptablesRules`` on a host that needs a sudo password.

This is a *usage* example, not part of the library. Where the plain fact
example (``examples/modules/iptables/iptables_iptables_rules.py``) reads
the ruleset over key-based SSH and lets the fact's own privilege ladder
stop at passwordless ``sudo -n``, this one shows the *elevated* rung of
that ladder actually carrying a credential: it attaches an inventory
``privilege`` section (``sudo_user``/``sudo_password``) to the RedHat
entries before gathering the fact, exactly as
``examples/actions/action_shell_sudo.py`` does for a bespoke command.

The read is two-tier (the module's privilege note):

1. A cheap in-shell probe runs ``iptables-save`` as the login user, and
   on failure retries under passwordless ``sudo -n``. On a host whose
   user has no passwordless sudo this whole probe is denied.
2. When the probe is *denied* -- recognised by an error signature such as
   "a password is required", never by empty output, since an empty
   ruleset is real state -- the fact re-reads through
   ``host.run_shell_privileged(command, sudo=True)``, which elevates with
   the inventory's ``sudo_password`` over a generated askpass helper, so
   the true ruleset comes back instead of the silent empty default that
   used to break rule/flush idempotence.

Privilege stays an *inventory* parameter: the ``gather_fact`` call names
neither user nor password, and the credential never reaches the parsed
fact or any log -- it lives only in the execution string sent over the
encrypted channel. If the host *rejects* the configured password, that
is sudo's own authentication failure and raises ``PrivilegeError`` (the
privilege counterpart of ``HostConnectionError``) rather than travelling
back as an empty ruleset; ``main`` catches it and prints one line naming
the host. A host without ``iptables-save`` installed raises
``CommandNotFoundError`` instead.

The hosts come from ``inventory.json`` in the current working directory,
parsed as JSON and validated with ``validate_inventory``; ``is_redhat``
(``host.family == "RedHat"``) filters the host list before the
``gather_fact`` fan-out, and the ``privilege`` section is attached only
to the RedHat entries so the credential is scoped to the group that
actually elevates. Edit the shared file there to target your machines and
put the login user's real password in ``PRIVILEGE``.

Run it with::

    uv run python examples/modules/iptables/iptables_iptables_rules_password_sudo.py
"""

import asyncio
import json
import sys
from pathlib import Path

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    IptablesRules,
    PrivilegeError,
    gather_fact,
    validate_inventory,
)

# The privilege section this demo attaches to the loaded RedHat inventory
# entries (the shared file carries none). The login user's ``sudo_password``
# authenticates the elevated re-read of the ruleset; ``sudo_user`` is the
# run-AS target -- ``root`` here, because only root may dump the live rules.
# ``root`` is sudo's own default target, so the elevated command is a bare
# ``sudo iptables-save``. The gather_fact call itself supplies neither.
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
        # Gather the fact across the guarded hosts concurrently. The login
        # probe is denied on a password-sudo host, so the fact falls through
        # to run_shell_privileged, which elevates with the sudo_password
        # above -- the true ruleset comes back rather than the empty default.
        rules = await gather_fact(hosts, IptablesRules)
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError) as exc:
        # Unreachable host, or iptables-save not installed (a
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
        print(f"== {host.name}: iptables.IptablesRules ==")
        print(json.dumps(rules[host.name], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
