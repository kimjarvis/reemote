# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Install and remove pip packages with ``pip install``/``uninstall``.

This is a *usage* example, not part of the library. It shows how an
application drives reemote's two-phase execution model end to end over a
**real SSH transport** (asyncssh):

1. Read the target hosts from ``inventory.json`` in the current working directory -- an
   inventory is just a list of dictionaries, each holding a host's name plus its SSH
   ``connection`` information (address, username and client key), which is exactly what
   JSON expresses -- and validate it up front with ``validate_inventory``, so a
   malformed file fails with an ``InventoryError`` naming the offending entry before any
   host is materialized or connected.
2. Build the deploy script as a generator yielding ``OperationRequest``
   objects, each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost`` via
``run``'s ``host_factory`` and never touches the network -- this example uses the
*default* factory (``Host.from_inventory``). Each plain ``Host`` lazily opens an
asyncssh connection from its ``connection`` data, gathers facts and runs commands for
real, and ``run`` closes those connections when it finishes. The inventory is read from
``inventory.json`` in the current working directory; edit it there to target your
machines.

``pip.packages`` is reemote's port of the baseline's ``pip.packages``: it
installs, upgrades or removes Python packages given as bare names or
``<pkg>==<version>`` pins, or applies a ``requirements`` file. The operation
is idempotent on the ``PipPackages`` fact (``<pip> freeze --all``): a pinned
request is present only when that *exact* version is installed, an unpinned
one when the name is installed at any version. The script yields the *same*
install request twice and the *same* removal request twice to show that
idempotence across the two phases: the prepare phase plans both halves of
each pair as changes (nothing is sent yet), while the apply phase runs the
first and reports the second as a no-op, because the fresh per-step fact
cache re-reads the freeze against the updated state. Installing and then
removing the same package makes the example *self-restoring*: it leaves the
environment as it found it (as long as the package was not installed
beforehand), which matters because ``run_examples.py`` sweeps every example
over the same shared hosts.

The pinned ``six==1.16.0`` rides through to the shell exactly as spelled
(``pip install six==1.16.0``, then ``pip uninstall --yes six`` -- removal
feeds pip bare names, since ``pip uninstall`` rejects ``==`` requirement
tokens), and every command -- the ``PipPackages`` freeze read included --
runs against the interpreter named by ``pip``. Here that is
``/tmp/reemote-pip-example-venv/bin/pip``, the virtualenv the sibling
``provision_pip.py`` provisions.

Managing a *virtualenv* is also why this example needs no ``sudo``: the
baseline's ``virtualenv``/``virtualenv_kwargs`` options are deliberately
dropped (venv creation belongs to the sibling ``pip.virtualenv``/``pip.venv``
operations, which a deploy expresses as its own request), and a venv is
targeted by passing its interpreter as
``pip=<venv>/bin/pip`` instead -- the deviation ``provision_pip.py``
provisions up front. A venv lives in the SSH user's own writable /tmp path, so all four
requests stay unprivileged; installing into a *system* interpreter's
site-packages would need root (``sudo=True``) and, on modern Ubuntu, a
``--break-system-packages`` extra argument, since PEP 668 marks that
interpreter externally managed. Like the other pip-facing examples, every
request carries ``when=is_ubuntu``: the shared ``inventory.json`` holds one
host in the ``"Ubuntu"`` family alongside hosts in other families, so each run
installs and removes on the Ubuntu host and skips every other one -- on a
host outside the group the driver skips each request without materializing
it, so neither the ``pip`` commands nor the ``PipPackages`` fact behind them
runs, and plan and log render ``[skipped: when guard is_ubuntu]``.

pip is *not* enough on its own on Ubuntu -- the stock ``ensurepip`` wheel
that ``python3 -m venv`` bootstraps ships in the separate
``python3-venv`` package -- so this example provisions its environment
itself before deploying: ``main`` first awaits ``provision_pip_venv`` from
the sibling ``provision_pip.py``, which installs ``python3-venv`` with
``apt.packages`` and creates the (``--clear``-ed, hence always fresh) venv
with a ``Shell`` action. The example therefore runs even on a host where
the venv was never created. Without that step the fact read
``/tmp/reemote-pip-example-venv/bin/pip freeze --all`` would exit 127 on the
missing interpreter and raise ``CommandNotFoundError`` before a single
change was planned.

Run it with::

    uv run python examples/modules/pip/pip_packages.py
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
    pip,
    run,
    validate_inventory,
)

# The pip provisioning helper sits beside this script; add its directory to
# sys.path so ``import provision_pip`` resolves whether the example is run
# by path or imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from provision_pip import VENV_PATH, provision_pip_venv

# The package to install and remove again -- the baseline's own documented
# example package, pinned so the idempotence is a version match rather than
# a bare name. A module-level tuple, so the request kwargs stay hashable and
# identical in the prepare and apply phases.
PACKAGES = ("six==1.16.0",)

# Every command, including the ``PipPackages`` freeze read, targets the
# virtualenv ``provision_pip.py`` creates -- the documented replacement for the
# dropped ``virtualenv`` option.
PIP_EXECUTABLE = f"{VENV_PATH}/bin/pip"



def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision and the
    determinism guard holds. The Ubuntu demo host is the one ``provision_pip.py``
    provisions with the example venv before this example deploys (see the
    module docstring), so the install/removal is guarded to that family; on a
    host outside it every request below would fail before doing anything, so
    the driver skips them all instead.
    """
    return host.family == "Ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Install the package -- twice, to show idempotence -- then remove it.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request installs whatever pin the ``PipPackages`` freeze does
    not carry yet; the second carries the same arguments and is a no-op in
    the apply phase once the first install froze it. The third removes the
    package again (``present=False``) and the fourth is its no-op repeat,
    leaving the venv as found so the shared-host example sweep is not
    polluted.

    The environment is the SSH user's own virtualenv, so no request needs
    elevation; managing a system interpreter's site-packages instead would
    flag them ``sudo=True``.
    """
    yield op(
        pip.packages,
        name="Install six",
        packages=PACKAGES,
        pip=PIP_EXECUTABLE,
        when=is_ubuntu,
    )
    yield op(
        pip.packages,
        name="Re-run install (no-op)",
        packages=PACKAGES,
        pip=PIP_EXECUTABLE,
        when=is_ubuntu,
    )
    yield op(
        pip.packages,
        name="Remove six again",
        packages=PACKAGES,
        present=False,
        pip=PIP_EXECUTABLE,
        when=is_ubuntu,
    )
    yield op(
        pip.packages,
        name="Re-run removal (no-op)",
        packages=PACKAGES,
        present=False,
        pip=PIP_EXECUTABLE,
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Provision the example venv on the Ubuntu hosts before anything
        # below reads or mutates pip state, so the example runs even on a
        # host where the venv was never created (see provision_pip.py).
        print("\n== Provisioning the pip venv (provision_pip.py) ==")
        await provision_pip_venv(inventory)

        # --dry: prepare phase only. Facts are still gathered over SSH (the
        # pip freeze read is read-only) so the plan reflects each host's real
        # pre-deployment state; no command that mutates a host is sent. A
        # request the ``when`` guard rejects is planned as
        # [skipped: when guard is_ubuntu] without materializing it, so its
        # fact is not read on that host either.
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
        # Python's own message already names the file and the reason, so
        # print just that -- no host was contacted and there is no traceback
        # worth showing.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema. The
        # message names the offending entry by its list index, which is the
        # whole report -- and it fails before any host is materialized.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library already reports every host-level failure as a typed
        # error whose message names the host and the failing command. Print
        # that message -- no traceback -- and exit non-zero so the failure
        # stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
