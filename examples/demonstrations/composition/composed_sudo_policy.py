# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: a site-wide privilege policy over unprivileged third-party roles.

This is a *usage* example, not part of the library. It builds on
``dnf_packages.py`` (same two-phase ``run`` flow over the real asyncssh
transport) and shows the third composition scenario: role functions written
elsewhere -- here, as if by a colleague targeting a root login -- that carry
no ``sudo`` flags at all, adopted under a site policy that connects as an
ordinary user and elevates everywhere.

The inventory is read from the project root's ``inventory.json`` (validated
up front with ``validate_inventory``), which holds hosts in the ``"centos"``
group alongside hosts in other groups. The same ``sub_deploy`` wrapper that
sets the elevation policy also carries ``when=is_centos``, so the redis role
is eligible only on the CentOS hosts; the driver skips it -- without reading
the ``DnfPackages`` fact -- on the Alpine and Ubuntu hosts. Privilege and
eligibility are independent axes: ``sudo`` fills the unflagged requests
(inner-wins), while ``when`` restricts who may run them at all (conjunction).

``sub_deploy(role_redis, sudo=True)`` retro-fits the policy without editing
the role: ``sudo=True`` is a *forced-elevation* policy that fills in every
request which has no opinion of its own. ``sudo``/``su`` are a tri-state
(``reemote.core.Privilege``: ``ON``/``OFF``/``UNSET``), so the inheritance
rule is *inner wins* -- a request that already set ``sudo=True`` (``ON``)
passes through untouched, an unflagged one (``UNSET``) is elevated by the
policy, and a request that deliberately set ``sudo=False`` (``OFF``) opts out
and stays unprivileged. That opt-out is exactly what a plain bool could not
express: a bool cannot tell "run this one unprivileged" from "never flagged",
so a ``sudo=True`` wrapper elevated both. The wrapping is a pure request-stream
transform (``dataclasses.replace`` over the frozen ``OperationRequest``), so
prepare and apply flatten identically and the determinism guard holds.

The role below shows all three states: the install omits the flag (``UNSET``,
elevated by the policy), the boot link already asks for ``sudo=True`` (``ON``,
preserved), and a healthcheck marker deliberately sets ``sudo=False`` (``OFF``)
to stay unprivileged even under the site-wide policy -- it only touches a
world-writable path, so it needs no root. In the run log the elevated commands
render with their ``sudo`` prefix (``$ sudo dnf install -y redis``) and the
opt-out does not (``$ touch /tmp/redis-managed``), since ``format_log`` maps
descriptors through the same ``command_shell`` the real transport uses.

Run it with::

    uv run python examples/demonstrations/composition/composed_sudo_policy.py
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
    compose,
    dnf,
    files,
    format_log,
    format_plan,
    op,
    run,
    sub_deploy,
    validate_inventory,
)

REDIS_UNIT = "/usr/lib/systemd/system/redis.service"
REDIS_WANTS_LINK = "/etc/systemd/system/multi-user.target.wants/redis.service"

# examples/demonstrations/composition/composed_sudo_policy.py -> the project
# root, so the inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of inventory entries -- dictionaries
    each carrying at least ``host`` and ``connection``. Returns the parsed
    list, ready to hand to ``run``, which materializes it with the default
    ``Host.from_inventory`` factory; raises ``InventoryError`` (via
    ``validate_inventory``) when the document is not an array or an entry
    does not match the schema.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_centos(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"centos"``.

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision. The redis role
    installs through ``dnf``, so the guard keeps it (and the facts it reads)
    off the Alpine and Ubuntu hosts.
    """
    return host.group == "centos"


def role_redis(host: Host) -> Generator[OperationRequest]:
    """A colleague's role, written for a root login: mostly no sudo flags.

    Do not "fix" it by sprinkling ``sudo=True`` through the body -- the
    point of the example is that the elevation policy lives at the
    composition site, not inside the role. The one ``sudo=False`` below is
    not a fix either: it is a deliberate opt-out for the single command that
    must stay unprivileged, which the tri-state lets the policy honour.
    """
    yield op(dnf.packages, name="Install redis", packages=("redis",))
    yield op(
        files.link,
        name="Enable redis at boot",
        path=REDIS_WANTS_LINK,
        target=REDIS_UNIT,
        sudo=True,  # the author did flag this one; the policy preserves it
    )
    yield op(
        files.file,
        name="Write healthcheck marker",
        path="/tmp/redis-managed",
        present=True,
        sudo=False,  # deliberate opt-out: stays unprivileged under the policy
    )


# The policy statement: everything this role yields runs elevated, and only
# on the CentOS hosts -- ``sudo`` fills the unflagged requests (inner-wins),
# ``when`` restricts eligibility (conjunction), both at the composition site.
deploy = compose(sub_deploy(role_redis, name_prefix="redis", sudo=True, when=is_centos))


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The plan already carries the wrapper's
        # names and privilege flags; no mutating command is sent. On the
        # non-CentOS hosts every step is [skipped: when guard is_centos].
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("\n== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
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
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed error
        # whose message names the host and the failing command. Print that
        # message -- no traceback -- and exit non-zero so the failure stays
        # script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
