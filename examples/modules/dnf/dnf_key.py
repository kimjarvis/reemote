# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Import a GPG key into the target host's rpm keyring, from a stream.

This is a *usage* example, not part of the library. It shows reemote5's
``dnf.key`` operation -- the one non-idempotent dnf operation -- driven end to
end over a **real SSH transport** (asyncssh):

1. Read ``inventory.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with ``validate_inventory``
   so a malformed file fails with an ``InventoryError`` naming the
   offending entry, *before* any host is materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects, each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``). Each plain ``Host``
lazily opens an asyncssh connection from its ``connection`` data, receives the
key over scp and runs the elevated import for real, and ``run`` closes those
connections when it finishes. The path is resolved from ``__file__``, so the
example works from any working directory; point ``INVENTORY_PATH`` at your
own file to target your machines.

``run`` is a coroutine (both phases are async), so ``main`` is async and
driven by ``asyncio.run``.

The source is an **IO-like object only** -- pyinfra's ``dnf.key`` takes a
``src`` that is a *filename or URL* the target host reads directly
(``rpm --import <src>``), but reemote5 deliberately has no controller-side
file locations, so a ``str`` ``src`` raises ``ValueError`` and the key content
must be handed over as a stream: here a module-level ``io.BytesIO``. Two rules
follow. The stream must be *seekable*: it is rewound to position 0 before it
is read. And because ``OperationRequest`` equality compares a stream
*by identity*, the deploy must pass the same object in both phases --
hence the module-level constant rather than a fresh ``BytesIO`` inside the
generator (a new object per phase would trip the determinism guard).

``dnf.key`` is **non-idempotent** -- like pyinfra's it reads no fact, always
reports a change and always imports (rpm itself deduplicates an
already-imported key), so the plan and the apply log agree. The one request
yields *two* commands at different privilege levels: a transfer that spools
the stream to a ``/tmp`` path over scp as the login user (transfers are never
elevated), then an elevated ``rpm --import`` of that spool file -- wrapped in
``sh -c`` so the import *and* its cleanup ``rm`` both run as root. That import
writes to the root-owned rpm keyring, so the request is flagged ``sudo=True``
and the driver elevates only the shell half, never the scp half.

The stream embeds the real Docker Release (CE rpm) signing key -- the armored
body of ``https://download.docker.com/linux/centos/gpg`` -- so ``rpm --import``
accepts it instead of rejecting placeholder bytes; swap in your own vendor
key's armor to trust a different repository. The dry run plans regardless
(nothing is sent), and the pytest suite never touches the network. Applying
for real needs an unconfined sudo: on a CentOS host whose sudo is confined to
SELinux ``staff_t``, the elevated ``sh -c 'rpm --import ...'`` wrapper cannot
read the login user's ``0600`` scp spool (no ``CAP_DAC_OVERRIDE``), so the
import fails with ``import read failed`` until the host is relaxed (see the
``dnf`` examples page for the full caveat).

The request also carries ``when=is_centos``, the declarative eligibility
guard (see ``examples/demonstrations/composition/composed_when_guard.py``).
The predicate reads only **static** inventory data -- ``host.group``, set by
the ``"group"`` key of the inventory entry -- and is a stable module-level
function, so the prepare and apply phases reach the same skip decision. The
driver skips a rejected request without materializing it, so no key is
transferred to -- and no ``rpm`` command run on -- a host without the
binary, and plan and log render the step as
``[skipped: when guard is_centos]`` in its lockstep slot. The shared
``inventory.json`` holds two hosts in the ``"centos"`` group alongside hosts
in other groups, so the run visibly imports on the CentOS hosts and skips
every other one.

Run it with::

    uv run python examples/modules/dnf/dnf_key.py
"""

import asyncio
import io
import json
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

from reemote5 import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    PrivilegeError,
    dnf,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# The IO-like key content, created once on the control machine: the same object
# is passed to the request in every phase, so the prepare and apply request
# sequences compare equal (OperationRequest compares it by identity). This is
# the real Docker Release (CE rpm) signing key, ASCII-armored -- the body of
# https://download.docker.com/linux/centos/gpg -- so ``rpm --import`` on the
# host accepts it instead of rejecting placeholder bytes. Replace the armor
# with your own vendor key to trust a different repository.
_DOCKER_RPM_KEY_ARMOR = """\
-----BEGIN PGP PUBLIC KEY BLOCK-----

mQINBFit5IEBEADDt86QpYKz5flnCsOyZ/fk3WwBKxfDjwHf/GIflo+4GWAXS7wJ
1PSzPsvSDATV10J44i5WQzh99q+lZvFCVRFiNhRmlmcXG+rk1QmDh3fsCCj9Q/yP
w8jn3Hx0zDtz8PIB/18ReftYJzUo34COLiHn8WiY20uGCF2pjdPgfxE+K454c4G7
gKFqVUFYgPug2CS0quaBB5b0rpFUdzTeI5RCStd27nHCpuSDCvRYAfdv+4Y1yiVh
KKdoe3Smj+RnXeVMgDxtH9FJibZ3DK7WnMN2yeob6VqXox+FvKYJCCLkbQgQmE50
uVK0uN71A1mQDcTRKQ2q3fFGlMTqJbbzr3LwnCBE6hV0a36t+DABtZTmz5O69xdJ
WGdBeePCnWVqtDb/BdEYz7hPKskcZBarygCCe2Xi7sZieoFZuq6ltPoCsdfEdfbO
+VBVKJnExqNZCcFUTEnbH4CldWROOzMS8BGUlkGpa59Sl1t0QcmWlw1EbkeMQNrN
spdR8lobcdNS9bpAJQqSHRZh3cAM9mA3Yq/bssUS/P2quRXLjJ9mIv3dky9C3udM
+q2unvnbNpPtIUly76FJ3s8g8sHeOnmYcKqNGqHq2Q3kMdA2eIbI0MqfOIo2+Xk0
rNt3ctq3g+cQiorcN3rdHPsTRSAcp+NCz1QF9TwXYtH1XV24A6QMO0+CZwARAQAB
tCtEb2NrZXIgUmVsZWFzZSAoQ0UgcnBtKSA8ZG9ja2VyQGRvY2tlci5jb20+iQI3
BBMBCgAhBQJYrep4AhsvBQsJCAcDBRUKCQgLBRYCAwEAAh4BAheAAAoJEMUv62ti
Hp816C0P/iP+1uhSa6Qq3TIc5sIFE5JHxOO6y0R97cUdAmCbEqBiJHUPNQDQaaRG
VYBm0K013Q1gcJeUJvS32gthmIvhkstw7KTodwOM8Kl11CCqZ07NPFef1b2SaJ7l
TYpyUsT9+e343ph+O4C1oUQw6flaAJe+8ATCmI/4KxfhIjD2a/Q1voR5tUIxfexC
/LZTx05gyf2mAgEWlRm/cGTStNfqDN1uoKMlV+WFuB1j2oTUuO1/dr8mL+FgZAM3
ntWFo9gQCllNV9ahYOON2gkoZoNuPUnHsf4Bj6BQJnIXbAhMk9H2sZzwUi9bgObZ
XO8+OrP4D4B9kCAKqqaQqA+O46LzO2vhN74lm/Fy6PumHuviqDBdN+HgtRPMUuao
xnuVJSvBu9sPdgT/pR1N9u/KnfAnnLtR6g+fx4mWz+ts/riB/KRHzXd+44jGKZra
IhTMfniguMJNsyEOO0AN8Tqcl0eRBxcOArcri7xu8HFvvl+e+ILymu4buusbYEVL
GBkYP5YMmScfKn+jnDVN4mWoN1Bq2yMhMGx6PA3hOvzPNsUoYy2BwDxNZyflzuAi
g59mgJm2NXtzNbSRJbMamKpQ69mzLWGdFNsRd4aH7PT7uPAURaf7B5BVp3UyjERW
5alSGnBqsZmvlRnVH5BDUhYsWZMPRQS9rRr4iGW0l+TH+O2VJ8aQ
=0Zqq
-----END PGP PUBLIC KEY BLOCK-----
"""
GPG_KEY = io.BytesIO(_DOCKER_RPM_KEY_ARMOR.encode("ascii"))

# examples/modules/dnf/dnf_key.py -> the project root, so the inventory is
# found whatever the current working directory is.
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
    so both deploy phases reach the same eligibility decision. The rpm
    keyring import below only makes sense on a CentOS host, so the guard
    keeps it off the Alpine and Ubuntu hosts.
    """
    return host.group == "centos"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Import the GPG key stream into each host's rpm keyring.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError),
    and identical on every host: eligibility is expressed with the
    ``when=is_centos`` guard the request carries, not with an ``if``.
    The single request is non-idempotent -- it always imports -- and yields
    two commands at different privilege levels: an unprivileged scp spool of
    ``GPG_KEY`` to a ``/tmp`` path, then the elevated ``rpm --import`` of that
    path. Importing writes to the root-owned rpm keyring, so the request is
    flagged ``sudo=True``; the scp transfer stays unprivileged regardless.
    """
    yield op(dnf.key, name="Import GPG key", src=GPG_KEY, sudo=True, when=is_centos)


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Nothing is transferred or imported, so the
        # plan shows the change (the operation is non-idempotent) without
        # touching a host. A request the ``when`` guard rejects is planned as
        # [skipped: when guard is_centos] without materializing it.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("\n== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH. Each
        # eligible host spools the key over scp, then runs the elevated
        # rpm --import and removes the spool file.
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
