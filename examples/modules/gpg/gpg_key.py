# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Install a GPG keyring file from a controller-side key stream.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Read ``inventory.json`` in the project root and parse it as JSON
   -- an inventory is just a list of dictionaries, which is exactly what
   JSON expresses -- then validate it up front with ``validate_inventory``
   so a malformed file fails with an ``InventoryError`` naming the
   offending entry, *before* any host is materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects, each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

``gpg.key`` installs a GPG key as a keyring file at an explicit ``dest``
path on the target host. The ``src`` is the key *content* as a seekable
IO-like stream on the control machine, never a filename or URL (fetch a
URL on the controller and pass its bytes). One request yields two
commands at different privilege levels: a ``GpgKeyUploadCommand``
transfer spools the stream to a fixed ``/tmp`` path over scp as the login
user, then a ``GpgKeyInstallCommand`` runs one elevated ``sh -c`` that
creates the destination's parent directory, dearmors the key with
``gpg --batch --yes --dearmor`` when the content is ASCII-armored (binary
content is copied verbatim), chmods the keyring ``0644`` and removes the
spool file. The request is flagged ``sudo=True`` for the install; the
transfer stays unprivileged regardless, so the log shows a plain ``scp``
line followed by ``sudo sh -c``.

Without a ``keyid`` the operation is idempotent on the destination's
*existence* (the baseline's no-keyid semantics): an existing file is left
untouched, whatever keys it holds. Pass ``keyid=...`` to compare the
actual keys held in the keyring instead, or ``present=False`` to remove
the file. For the APT repository-keyring use case with its ``/etc/apt/
keyrings/`` defaults, ``apt.key`` is the specialized operation.

**The stream below carries a real public key** -- the Docker CE release
signing key for CentOS/rpm (fingerprint
``060A61C51B558A7F742B77AAC52FEB6B621E9F35``, published at
``https://download.docker.com/linux/centos/gpg``) -- so the dearmor has
valid material to convert. The keyring still lands under
``/etc/apt/keyrings/`` to demonstrate the explicit ``dest``; swap in your
own vendor key and path when deploying elsewhere.

One host-side caveat: ``/etc/apt/keyrings/`` is ``etc_t``, which a CentOS
host's SELinux ``staff_t``-confined sudo can neither create nor unlink. On
the demo hosts a keyring from an earlier unconfined run already exists, so
the existence-idempotent request no-ops and the script exits 0; a *fresh*
install into that directory is denied until the host's sudo is unconfined
(or SELinux is permissive) -- the same confinement the ``selinux``,
``sysctl`` and ``dnf.key`` examples document. ``gpg.import_key`` (which
writes root's keychain, not ``/etc``) is unaffected and applies for real.

The stream is compared *by identity* across the prepare and apply
phases, so it is defined once at module level below -- building a fresh
stream inside the deploy generator would diverge between the phases and
raise ``DeterminismError``.

The deploy yields the same install request twice: in the prepare phase
(where nothing is sent) both plan as changes, but in the apply phase the
second request's fresh per-step fact probe sees the file the first
install created and reports it already exists.

Every request carries ``when=is_centos``, the declarative eligibility
guard: the predicate reads only **static** inventory data --
``host.group``, set by the ``"group"`` key of the inventory entry -- and
is a stable module-level function, so the prepare and apply phases reach
the same skip decision. The driver skips a rejected request without
materializing it, so on hosts outside the ``"centos"`` group neither the
commands nor the facts behind them run, and plan and log render the step
as ``[skipped: when guard is_centos]`` in its lockstep slot. The path is
resolved from ``__file__``, so the example works from any working
directory; point ``INVENTORY_PATH`` at your own file to target your
machines.

Run it with::

    uv run python examples/modules/gpg/gpg_key.py
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
    format_log,
    format_plan,
    gpg,
    op,
    run,
    validate_inventory,
)

# The key content, as ONE module-level stream: OperationRequest compares the
# IO object by identity, so both phases must pass the same object (a stream
# built inside the generator would trip the determinism guard). Real armored
# bytes: the Docker CE release signing key for CentOS.
GPG_KEY = io.BytesIO(
    b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
    b"\n"
    b"mQINBFit5IEBEADDt86QpYKz5flnCsOyZ/fk3WwBKxfDjwHf/GIflo+4GWAXS7wJ\n"
    b"1PSzPsvSDATV10J44i5WQzh99q+lZvFCVRFiNhRmlmcXG+rk1QmDh3fsCCj9Q/yP\n"
    b"w8jn3Hx0zDtz8PIB/18ReftYJzUo34COLiHn8WiY20uGCF2pjdPgfxE+K454c4G7\n"
    b"gKFqVUFYgPug2CS0quaBB5b0rpFUdzTeI5RCStd27nHCpuSDCvRYAfdv+4Y1yiVh\n"
    b"KKdoe3Smj+RnXeVMgDxtH9FJibZ3DK7WnMN2yeob6VqXox+FvKYJCCLkbQgQmE50\n"
    b"uVK0uN71A1mQDcTRKQ2q3fFGlMTqJbbzr3LwnCBE6hV0a36t+DABtZTmz5O69xdJ\n"
    b"WGdBeePCnWVqtDb/BdEYz7hPKskcZBarygCCe2Xi7sZieoFZuq6ltPoCsdfEdfbO\n"
    b"+VBVKJnExqNZCcFUTEnbH4CldWROOzMS8BGUlkGpa59Sl1t0QcmWlw1EbkeMQNrN\n"
    b"spdR8lobcdNS9bpAJQqSHRZh3cAM9mA3Yq/bssUS/P2quRXLjJ9mIv3dky9C3udM\n"
    b"+q2unvnbNpPtIUly76FJ3s8g8sHeOnmYcKqNGqHq2Q3kMdA2eIbI0MqfOIo2+Xk0\n"
    b"rNt3ctq3g+cQiorcN3rdHPsTRSAcp+NCz1QF9TwXYtH1XV24A6QMO0+CZwARAQAB\n"
    b"tCtEb2NrZXIgUmVsZWFzZSAoQ0UgcnBtKSA8ZG9ja2VyQGRvY2tlci5jb20+iQI3\n"
    b"BBMBCgAhBQJYrep4AhsvBQsJCAcDBRUKCQgLBRYCAwEAAh4BAheAAAoJEMUv62ti\n"
    b"Hp816C0P/iP+1uhSa6Qq3TIc5sIFE5JHxOO6y0R97cUdAmCbEqBiJHUPNQDQaaRG\n"
    b"VYBm0K013Q1gcJeUJvS32gthmIvhkstw7KTodwOM8Kl11CCqZ07NPFef1b2SaJ7l\n"
    b"TYpyUsT9+e343ph+O4C1oUQw6flaAJe+8ATCmI/4KxfhIjD2a/Q1voR5tUIxfexC\n"
    b"/LZTx05gyf2mAgEWlRm/cGTStNfqDN1uoKMlV+WFuB1j2oTUuO1/dr8mL+FgZAM3\n"
    b"ntWFo9gQCllNV9ahYOON2gkoZoNuPUnHsf4Bj6BQJnIXbAhMk9H2sZzwUi9bgObZ\n"
    b"XO8+OrP4D4B9kCAKqqaQqA+O46LzO2vhN74lm/Fy6PumHuviqDBdN+HgtRPMUuao\n"
    b"xnuVJSvBu9sPdgT/pR1N9u/KnfAnnLtR6g+fx4mWz+ts/riB/KRHzXd+44jGKZra\n"
    b"IhTMfniguMJNsyEOO0AN8Tqcl0eRBxcOArcri7xu8HFvvl+e+ILymu4buusbYEVL\n"
    b"GBkYP5YMmScfKn+jnDVN4mWoN1Bq2yMhMGx6PA3hOvzPNsUoYy2BwDxNZyflzuAi\n"
    b"g59mgJm2NXtzNbSRJbMamKpQ69mzLWGdFNsRd4aH7PT7uPAURaf7B5BVp3UyjERW\n"
    b"5alSGnBqsZmvlRnVH5BDUhYsWZMPRQS9rRr4iGW0l+TH+O2VJ8aQ\n"
    b"=0Zqq\n"
    b"-----END PGP PUBLIC KEY BLOCK-----\n"
)

# examples/modules/gpg/gpg_key.py -> the project root, so the inventory
# is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of inventory entries -- dictionaries
    each carrying at least ``host`` and ``connection``. Returns the parsed
    list, ready to materialize into hosts; raises ``InventoryError`` (via
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
    so both deploy phases reach the same eligibility decision.
    """
    return host.group == "centos"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Install the key stream as ``/etc/apt/keyrings/reemote5-demo.gpg``, twice.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).

    Writing under ``/etc/apt/keyrings/`` needs root, so every request is
    flagged ``sudo=True``; the driver elevates the install shell while the
    spool transfer rides scp as the login user.
    """
    yield op(
        gpg.key,
        name="Install demo GPG keyring",
        src=GPG_KEY,
        dest="/etc/apt/keyrings/reemote5-demo.gpg",
        sudo=True,
        when=is_centos,
    )
    yield op(
        gpg.key,
        name="Install demo GPG keyring (again)",
        src=GPG_KEY,
        dest="/etc/apt/keyrings/reemote5-demo.gpg",
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no command
        # that mutates a host is sent.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan (prepare phase, --dry) ==")
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