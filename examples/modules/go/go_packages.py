# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Install and remove Go binaries with ``go install`` and ``rm``.

This is a *usage* example, not part of the library. It shows how an application
drives reemote's two-phase execution model end to end over a **real SSH
transport** (asyncssh):

1. Read the target hosts from ``inventory.json`` in the current working
   directory -- an inventory is just a list of dictionaries, each holding a
   host's name plus its SSH ``connection`` information (address, username and
   client key), which is exactly what JSON expresses -- and validate it up front
   with ``validate_inventory``, so a malformed file fails with an
   ``InventoryError`` naming the offending entry before any host is materialized
   or connected.
2. Build the deploy script as a generator yielding ``OperationRequest`` objects,
   each guarded by a ``when`` predicate.
3. Run the prepare phase to get a plan, and the apply phase to execute it.

Unlike the pytest suite -- which injects the test framework's ``SimulatedHost``
via ``run``'s ``host_factory`` and never touches the network -- this example uses
the *default* factory (``Host.from_inventory``). Each plain ``Host`` lazily opens
an asyncssh connection from its ``connection`` data, gathers facts and runs
commands for real, and ``run`` closes those connections when it finishes. The
inventory is read from ``inventory.json`` in the current working directory; edit
it there to target your machines.

``go.packages`` is reemote's port of the baseline's ``go.packages``: it installs,
upgrades or removes the ``go install`` binaries on a host, given as bare import
paths or ``<pkg>=<version>`` pins. The operation is idempotent on the
``GoPackages`` fact (the ``go version -m`` listing of the go bin directory): a
pinned request is present only when that *exact* version is installed, while an
unpinned one -- and one pinned to the special ``latest`` -- is present when the
path is installed at any version, so re-running is a no-op. The script yields
the *same* install request twice and the *same* removal request twice to show
that idempotence across the two phases: the prepare phase plans both halves of
each pair as changes (nothing is sent yet), while the apply phase runs the first
and reports the second as a no-op, because the fresh per-step fact cache
re-reads the listing against the updated state. Installing and then removing the
same tools makes the example *self-restoring*: it leaves the host's go bin
directory as it found it (as long as the tools were not installed beforehand),
which matters because ``run_examples.py`` sweeps every example over the same
shared hosts.

The two tools show both presence rules.
``honnef.co/go/tools/cmd/staticcheck=v0.4.5`` is reemote's one user-facing pin
spelling -- shared with apt, dnf, apk and npm -- and rides through to the
install shell translated into go's own grammar as ``staticcheck@v0.4.5``
(pyinfra's ``@`` input spelling is deliberately not accepted);
``mvdan.cc/gofumpt`` is unpinned, so it is installed at whatever version
``@latest`` resolves to and considered present at any version afterwards.

Removal is name-based, the baseline verbatim and deliberately unlike the
version-aware removal of ``apt``/``gem``: a compiled binary carries no
per-version state, so ``rm -f`` cannot select or spare a version. The removal
command resolves the bin directory in the shell (``GOBIN``, else
``$GOPATH/bin`` -- the fact's own guard) and drops ``"$BINDIR"/<binary>`` for
each path, where the binary name strips a trailing Semantic-Import-Versioning
``/vN`` segment.

No request flags ``sudo``: ``go install`` and the removal both act on the
*connecting user's* go tree, and elevating them would install into root's
``GOPATH`` -- a tree the unprivileged ``GoPackages`` read never sees. One
behavior is demonstrated by the pytest suite rather than here: ``latest=True``
keeps the baseline's documented non-idempotence, re-issuing
``go install <pkg>@latest`` for every installed unpinned request and reporting
changed on every run, because there is no registry to compare against offline.

Like the other examples, every request carries ``when=is_ubuntu``: the shared
``inventory.json`` holds one host in the ``"Ubuntu"`` family alongside hosts in
other families, so each run installs and removes on the Ubuntu host and skips
every other one -- on a host outside the group the driver skips each request
without materializing it, so plan and log render ``[skipped: when guard
is_ubuntu]``. That guard is not cosmetic: the fact read is a compound command
that is self-guarding (a host without go simply answers ``[]``), but the
install shells are not, so on an unprovisioned host they would fail with
``CommandNotFoundError``. go is *not* installed by default on the demo host --
Ubuntu ships no ``go`` until you ask -- so this example provisions the tooling
itself before deploying: ``main`` first awaits ``provision_go`` from the sibling
``provision_go.py``, which installs the ``golang-go`` deb with ``apt.packages``
(idempotent, so the run is cheap on a host that already has it). The example
therefore runs even on a host where go was never installed.

Run it with::

    uv run python examples/modules/go/go_packages.py
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
    go,
    op,
    run,
    validate_inventory,
)

# The go provisioning helper sits beside this script; add its directory to
# sys.path so ``import provision_go`` resolves whether the example is run by
# path or imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from provision_go import provision_go

# The tools to install and remove again -- one ``<pkg>=<version>`` pin and one
# bare import path, so the run shows both of the fact's presence rules. A
# module-level tuple, so the request kwargs stay hashable and identical in the
# prepare and apply phases.
PACKAGES = ("honnef.co/go/tools/cmd/staticcheck=v0.4.5", "mvdan.cc/gofumpt")


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows so
    both deploy phases reach the same eligibility decision and the determinism
    guard holds. The Ubuntu demo host is the one ``provision_go.py`` installs
    the go toolchain on before this example deploys (see the module docstring),
    so the install/removal is guarded to that family; on a host outside it the
    bare ``go install`` shells would fail before doing anything, so the driver
    skips them all instead.
    """
    return host.family == "Ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Install the tools -- twice, to show idempotence -- then remove them.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases (a branch on a mutable fact would raise DeterminismError). The first
    request installs whatever the ``GoPackages`` listing does not carry yet; the
    second carries the same arguments and is a no-op in the apply phase once the
    first install landed. The third removes the tools again (``present=False``)
    and the fourth is its no-op repeat, leaving the go bin directory as found
    so the shared-host example sweep is not polluted.

    No request is elevated: both commands work on the connecting user's go
    tree, and ``sudo`` would install into root's ``GOPATH`` instead.
    """
    yield op(
        go.packages,
        name="Install staticcheck and gofumpt",
        packages=PACKAGES,
        when=is_ubuntu,
    )
    yield op(
        go.packages,
        name="Re-run install (no-op)",
        packages=PACKAGES,
        when=is_ubuntu,
    )
    yield op(
        go.packages,
        name="Remove them again",
        packages=PACKAGES,
        present=False,
        when=is_ubuntu,
    )
    yield op(
        go.packages,
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

        # Install the go toolchain on the Ubuntu hosts before anything below
        # reads or mutates go state, so the example runs even on a host where
        # go was never installed (see provision_go.py).
        print("\n== Provisioning golang-go (provision_go.py) ==")
        await provision_go(inventory)

        # --dry: prepare phase only. Facts are still gathered over SSH (the
        # go bin directory listing is read-only) so the plan reflects each
        # host's real pre-deployment state; no command that mutates a host is
        # sent. A request the ``when`` guard rejects is planned as
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
        # whose message names the host and the failing command (a missing go
        # binary surfaces as CommandNotFoundError, a CommandError subclass --
        # only the *fact* read swallows it, never the install shells). Print
        # that message -- no traceback -- and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
