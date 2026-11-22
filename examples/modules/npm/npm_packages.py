# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Install and remove global npm packages with ``npm install``/``uninstall``.

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

``npm.packages`` is reemote's port of the baseline's ``npm.packages``,
extended to the surface the Ansible ``npm`` module needs: it installs,
upgrades or removes Node.js packages given as bare names or ``<pkg>=<version>``
pins, globally (the default, npm's ``-g``) or inside one project directory, and
can apply a ``package.json`` manifest. The operation is idempotent on the
``NpmPackages`` fact (``npm ls -g --depth=0 --json``): a pinned request is
present only when that *exact* version is installed and an unpinned one when the
name is installed at any version, so re-running is a no-op. The script yields
the *same* install request twice and the *same* removal request twice to show
that idempotence across the two phases: the prepare phase plans both halves of
each pair as changes (nothing is sent yet), while the apply phase runs the
first and reports the second as a no-op, because the fresh per-step fact cache
re-reads the listing against the updated state. Installing and then removing the
same packages makes the example *self-restoring*: it leaves the host's global
tree as it found it (as long as they were not installed beforehand), which
matters because ``run_examples.py`` sweeps every example over the same shared
hosts.

The two packages show both presence rules. ``minimist=1.2.8`` is reemote's one
user-facing pin spelling -- shared with apt, dnf and apk -- and rides through to
the shell translated into npm's own grammar as ``minimist@1.2.8`` (pyinfra's
``<pkg>@<version>`` input spelling is deliberately not accepted); ``ms`` is
unpinned, so it is installed once and considered present at whatever version npm
reports afterwards. Removal feeds npm the tokens verbatim -- ``npm uninstall -g
minimist@1.2.8 ms`` -- because, unlike ``pip uninstall``, npm accepts a versioned
token. Both are pure JavaScript with no lifecycle scripts, so the install is a
registry fetch and a file write rather than a native build.

Every request flags ``sudo=True``, because a global install writes into npm's
prefix -- ``/usr/local/lib/node_modules``, root-owned on Ubuntu -- and the
driver elevates the rendered shell (``sudo npm install -g ...``). The
``NpmPackages`` read is unprivileged: it only lists that tree. Two further
parameters are *not* set here, and both exist for the cases this demo does not
have: ``prefix`` renders ``NPM_CONFIG_PREFIX`` to relocate (and so separately
track) a global tree, which matters when a host keeps more than one, and
``unsafe_perm`` renders ``NPM_CONFIG_UNSAFE_PERM=true`` to let npm run a
dependency's lifecycle scripts while elevated -- neither package here ships one.
The project scope (``global_=False`` with ``directory=``) and the stateless
``package_json=`` manifest direction are exercised by the pytest suite and
described on the module page instead of here, because building a real project
tree would leave ``node_modules`` behind on the shared host -- exactly what the
self-restoring cycle above exists to avoid.

Like the other examples, every request carries ``when=is_ubuntu``: the shared
``inventory.json`` holds one host in the ``"Ubuntu"`` family alongside hosts in
other families, so each run installs and removes on the Ubuntu host and skips
every other one -- on a host outside the group the driver skips each request
without materializing it, so neither the ``npm`` commands nor the
``NpmPackages`` fact behind them runs, and plan and log render
``[skipped: when guard is_ubuntu]``. That guard is not cosmetic: the fact read
is a bare ``npm`` invocation, because the baseline's ``requires_command``
gating is deliberately dropped, so on a host without npm it would exit 127 and
raise ``CommandNotFoundError``.

npm is *not* installed by default on the demo host -- Ubuntu ships no
``node``/``npm`` until you ask -- so this example provisions the tooling itself
before deploying: ``main`` first awaits ``provision_npm`` from the sibling
``provision_npm.py``, which installs the ``nodejs`` and ``npm`` debs with
``apt.packages`` (idempotent, so the run is cheap on a host that already has
them). The example therefore runs even on a host where npm was never installed.

Run it with::

    uv run python examples/modules/npm/npm_packages.py
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
    npm,
    op,
    run,
    validate_inventory,
)

# The npm provisioning helper sits beside this script; add its directory to
# sys.path so ``import provision_npm`` resolves whether the example is run
# by path or imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from provision_npm import provision_npm

# The packages to install and remove again -- one ``<pkg>=<version>`` pin and
# one bare name, so the run shows both of the fact's presence rules. A
# module-level tuple, so the request kwargs stay hashable and identical in the
# prepare and apply phases.
PACKAGES = ("minimist=1.2.8", "ms")



def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision and the
    determinism guard holds. The Ubuntu demo host is the one
    ``provision_npm.py`` installs npm on before this example deploys (see the
    module docstring), so the install/removal is guarded to that family; on a
    host outside it the bare ``npm`` fact read would fail before doing
    anything, so the driver skips them all instead.
    """
    return host.family == "Ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Install the packages -- twice, to show idempotence -- then remove them.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    The first request installs whatever the ``NpmPackages`` listing does not
    carry yet; the second carries the same arguments and is a no-op in the
    apply phase once the first install landed. The third removes the packages
    again (``present=False``) and the fourth is its no-op repeat, leaving the
    global tree as found so the shared-host example sweep is not polluted.

    A global tree lives under npm's root-owned prefix, so every request is
    elevated; a project directory owned by the login user would not need
    ``sudo=True``.
    """
    yield op(
        npm.packages,
        name="Install minimist and ms globally",
        packages=PACKAGES,
        sudo=True,
        when=is_ubuntu,
    )
    yield op(
        npm.packages,
        name="Re-run install (no-op)",
        packages=PACKAGES,
        sudo=True,
        when=is_ubuntu,
    )
    yield op(
        npm.packages,
        name="Remove them again",
        packages=PACKAGES,
        present=False,
        sudo=True,
        when=is_ubuntu,
    )
    yield op(
        npm.packages,
        name="Re-run removal (no-op)",
        packages=PACKAGES,
        present=False,
        sudo=True,
        when=is_ubuntu,
    )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Install nodejs and npm on the Ubuntu hosts before anything below
        # reads or mutates npm state, so the example runs even on a host
        # where npm was never installed (see provision_npm.py).
        print("\n== Provisioning npm (provision_npm.py) ==")
        await provision_npm(inventory)

        # --dry: prepare phase only. Facts are still gathered over SSH (the
        # npm ls read is read-only) so the plan reflects each host's real
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
        # error whose message names the host and the failing command (a
        # missing npm binary surfaces as CommandNotFoundError, a
        # CommandError subclass). Print that message -- no traceback -- and
        # exit non-zero so the failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
