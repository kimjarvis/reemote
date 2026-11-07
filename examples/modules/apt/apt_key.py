# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Install an APT GPG keyring file from a controller-side key stream.

This is a *usage* example, not part of the library. It shows how an
application drives reemote5's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Read the target hosts from ``inventory.json`` in the project root -- an
   inventory is just a list of dictionaries, each holding a host's name plus
   its SSH ``connection`` information (address, username and client key),
   which is exactly what JSON expresses -- and validate it up front with
   ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is
   materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects, each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example
uses the *default* factory (``Host.from_inventory``). Each plain ``Host``
lazily opens an asyncssh connection from its ``connection`` data, gathers
facts and runs commands for real, and ``run`` closes those connections when
it finishes. The path is resolved from ``__file__``, so the example works
from any working directory; point ``INVENTORY_PATH`` at your own file to
target your machines.

``run`` is a coroutine (both phases are async), so ``main`` is async and
driven by ``asyncio.run``.

``apt.key`` installs a GPG key as a keyring file under ``/etc/apt/keyrings/``
-- the modern replacement for the deprecated ``apt-key add``: a sources
entry references the file via ``signed-by=`` so the key is trusted only for
its own repository. The ``src`` is the key *content* as a seekable IO-like
stream on the control machine, never a filename or URL (fetch a URL on the
controller and pass its bytes); ``dest`` names the keyring file, resolved
under ``/etc/apt/keyrings/`` when relative.

One request yields two commands at different privilege levels: an
``AptKeyUploadCommand`` transfer spools the stream to a fixed ``/tmp`` path
over scp as the login user, then an ``AptKeyInstallCommand`` runs one
elevated ``sh -c`` that creates the keyrings directory, dearmors the key
with ``gpg --batch --dearmor`` when the content is ASCII-armored (binary
content is copied verbatim), chmods the keyring ``0644`` and removes the
spool file. The request is flagged ``sudo=True`` for the install; the
transfer stays unprivileged regardless, so the log shows a plain ``scp``
line followed by ``sudo sh -c``.

Unlike pyinfra's existence-checked ``apt.key``, this one is deliberately
**non-idempotent** (like ``dnf.key``): it always installs and always reports
a change, on every run. The stream is compared *by identity* across the
prepare and apply phases, so it is defined once at module level below --
building a fresh stream inside the deploy generator would diverge between
the phases and raise ``DeterminismError``. The placeholder bytes stand in
for a real vendor key (e.g. the armored body of
``https://download.docker.com/linux/ubuntu/gpg``); replace them with the
key you actually deploy.

Like the other apt examples, the request carries ``when=is_ubuntu``: the
predicate reads only static inventory data (``host.group``) and is a stable
module-level function, so both phases reach the same skip decision. The
driver skips a rejected request without materializing it -- neither the
scp spool transfer nor the elevated install runs on a non-Ubuntu host,
whose package manager would never read an ``/etc/apt/keyrings`` file -- and
plan and log render ``[skipped: when guard is_ubuntu]``. The shared
``inventory.json`` holds one host in the ``"ubuntu"`` group alongside hosts
in other groups, so each run installs on the Ubuntu host and skips every
other one.

Run it with::

    uv run python examples/modules/apt/apt_key.py
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
    apt,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# examples/modules/apt/apt_key.py -> the project root, so the inventory is
# found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The key content, as ONE module-level stream: OperationRequest compares the
# IO object by identity, so both phases must pass the same object (a stream
# built inside the generator would trip the determinism guard). Placeholder
# armored bytes -- replace with a real vendor key before deploying.
GPG_KEY = io.BytesIO(
    b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
    b"mQIN reemote5 example placeholder key material\n"
    b"-----END PGP PUBLIC KEY BLOCK-----\n"
)


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


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"ubuntu"``.

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision and the
    determinism guard holds. apt itself is the reason for the guard: the
    keyring the request writes under ``/etc/apt/keyrings/`` is only read by
    apt, which a non-Ubuntu host does not run, so the driver skips it there.
    """
    return host.group == "ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Install the key stream as ``/etc/apt/keyrings/reemote5-demo.gpg``.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases. Writing under ``/etc/apt/keyrings/`` needs root, so the
    request is flagged ``sudo=True``; the driver elevates the install shell
    while the spool transfer rides scp as the login user.
    """
    yield op(
        apt.key,
        name="Install demo apt GPG keyring",
        src=GPG_KEY,
        dest="reemote5-demo.gpg",
        sudo=True,
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. The operation reads no fact (it is
        # non-idempotent), so the plan always shows the install as a change;
        # no command that mutates a host is sent. A request the ``when``
        # guard rejects is planned as [skipped: when guard is_ubuntu]
        # without materializing it.
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