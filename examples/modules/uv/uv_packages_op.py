# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Install and remove uv-managed packages with ``uv pip install``/``uninstall``.

This is a *usage* example, not part of the library. It drives reemote's
two-phase execution model end to end over a **real SSH transport** (asyncssh):

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- a list of dictionaries, each a host's name plus its SSH
   ``connection`` data -- and validate it up front with ``validate_inventory``
   so a malformed file fails with an ``InventoryError`` before any host is
   materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects, each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects ``SimulatedHost`` via ``run``'s
``host_factory`` and never touches the network -- this example uses the
*default* factory (``Host.from_inventory``); each ``Host`` lazily opens an
asyncssh connection, gathers facts and runs commands for real, and ``run``
closes those connections when it finishes.

``uv.packages`` is reemote's port of pyinfra's ``uv.packages``: it installs,
upgrades or removes Python packages given as bare names or ``<pkg>==<version>``
pins, or applies a ``requirements`` file. The operation is idempotent on the
``uv.Packages`` fact (``uv pip list --format json``): a pinned request is
present only when that *exact* version is installed, an unpinned one when the
name is installed at any version, and a request and a fact entry meet at the
*lowercased* name (uv prints each distribution's canonical name, so there is no
PEP 503 folding here as ``pip.packages`` does). The script yields the *same*
install request twice and the *same* removal request twice to show that
idempotence across the two phases: prepare plans both halves of each pair as
changes (nothing is sent yet), while apply runs the first and reports the
second as a no-op, because the fresh per-step fact cache re-reads the listing
against the updated state. Installing and then removing the same package makes
the example *self-restoring*: it leaves the environment as it found it, which
matters because ``run_examples.py`` sweeps every example over the same shared
hosts.

The pinned ``six==1.16.0`` rides through to the shell exactly as spelled
(``uv pip install --no-progress six==1.16.0``, then ``uv pip uninstall
--no-progress six`` -- removal feeds uv *bare names*, since ``uv pip uninstall``
rejects ``==`` requirement tokens), and every command keeps pyinfra's
``--no-progress`` so uv never repaints a progress bar into captured stdout.

Which environment uv manages is a choice rather than an inference: leave
``venv`` unset and uv applies its own resolution rule -- an active or discovered
``.venv``, or the interpreter uv falls back to -- or name one with
``venv="/srv/app/.venv"``. The latter is a virtual environment's root directory,
an interpreter path or a version request, passed as uv's ``--python`` to the
``Packages`` read *and* to every command, so the listing the operation compares
against is the very environment it mutates. This script keeps to the unscoped
form: a scoped demo on the shared host would have to create the venv before
``run`` gathers facts, since ``uv pip list --python <missing>`` exits 2 on the
target and the prepare read would fail. The sibling ``uv_venv.py`` covers
creation, and ``uv_packages_venv.py`` joins the two inside a container -- it
bootstraps uv, creates the venv and then drives the scoped request, on
``run_waves`` so every step lands before the next one is planned. Installing is
per-user and unprivileged, so no request carries ``sudo``; managing a *system*
interpreter's site-packages would need root. Like the sibling uv examples, every
request carries ``when=is_ubuntu``: the shared ``inventory.json`` holds one host
in the ``"Ubuntu"`` family alongside hosts in other families, so the run installs
and removes on the Ubuntu host and skips every other one -- a host outside the
group has each request skipped without materializing it, so neither the uv
commands nor the ``Packages`` fact behind them runs, and plan and log render
``[skipped: when guard is_ubuntu]``.

uv is *not* enough on its own on Ubuntu -- the noble apt suite ships no ``uv``
package -- so this example provisions the tool itself before deploying: ``main``
first awaits ``provision_uv`` from the sibling ``provision_uv.py`` (an
idempotent ``snap.package`` install of Astral's ``astral-uv`` snap). Without
that step the fact read ``uv pip list --format json`` would exit 127 on the
missing tool and raise ``CommandNotFoundError`` before a single change was
planned.

Run it with::

    uv run python examples/modules/uv/uv_packages_op.py
"""

import asyncio
import json
import sys
from collections.abc import Generator
from pathlib import Path

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    OperationRequest,
    PrivilegeError,
    format_log,
    format_plan,
    op,
    run,
    uv,
    validate_inventory,
)

# The uv provisioning helper sits beside this script; add its directory to
# sys.path so ``import provision_uv`` resolves whether the example is run by
# path or imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from provision_uv import provision_uv

# The package to install and remove again -- pinned so the idempotence is a
# version match rather than a bare name. A module-level tuple, so the request
# kwargs stay hashable and identical in the prepare and apply phases.
PACKAGES = ("six==1.16.0",)


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows so
    both deploy phases reach the same eligibility decision and the determinism
    guard holds. The Ubuntu demo host is the one ``provision_uv.py`` installs uv
    on before this example deploys (see the module docstring), so the
    install/removal is guarded to that family.
    """
    return host.family == "Ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Install the package -- twice, to show idempotence -- then remove it.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases (a branch on a mutable fact would raise DeterminismError). The first
    request installs whatever pin the ``Packages`` listing does not carry yet;
    the second carries the same arguments and is a no-op in the apply phase once
    the first install landed it. The third removes the package again
    (``present=False``) and the fourth is its no-op repeat, leaving the
    environment as found so the shared-host example sweep is not polluted.

    uv installs into the user's resolved environment, so no request needs
    elevation; a system interpreter's site-packages would flag ``sudo=True``.
    """
    yield op(
        uv.packages,
        name="Install six",
        packages=PACKAGES,
        when=is_ubuntu,
    )
    yield op(
        uv.packages,
        name="Re-run install (no-op)",
        packages=PACKAGES,
        when=is_ubuntu,
    )
    yield op(
        uv.packages,
        name="Remove six again",
        packages=PACKAGES,
        present=False,
        when=is_ubuntu,
    )
    yield op(
        uv.packages,
        name="Re-run removal (no-op)",
        packages=PACKAGES,
        present=False,
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Install uv on the Ubuntu hosts before anything below reads or mutates
        # uv state, so the example runs even on a host where uv was never
        # installed (see provision_uv.py).
        print("\n== Provisioning uv (provision_uv.py) ==")
        await provision_uv(inventory)

        # --dry: prepare phase only. Facts are still gathered over SSH (the
        # ``uv pip list`` read is read-only) so the plan reflects each host's
        # real pre-deployment state; no command that mutates a host is sent. A
        # request the ``when`` guard rejects is planned as
        # [skipped: when guard is_ubuntu] without materializing it.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("\n== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Run log (apply phase) ==")
        assert report is not None
        print(format_log(report))
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema. The
        # message names the offending entry by its list index, and it fails
        # before any host is materialized.
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
