# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Compile a Go package into a binary, idempotent against its build info.

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

``go.build`` compiles a package into ``output_path`` with ``go build``,
idempotent against the ``GoBinaryBuildInfo`` fact: a missing binary, an
embedded compiler version that no longer matches the current toolchain, or
``force=True`` each rebuild; an up-to-date binary is a no-op. To keep the demo
self-contained and *self-restoring*, the deploy first writes a two-file
``hello`` module under ``/tmp`` (``files.directory`` plus two ``files.put``
transfers), builds it, re-asserts the same build request -- the guaranteed
apply-phase no-op, since the fresh per-step fact cache reads the binary's
embedded ``go`` version back against the toolchain that just built it -- and
finally ``files.directory(present=False)`` removes the whole tree, binary
included, leaving the host as it was found.

Two deviations from the design sketch shape the requests. The operation takes
**no ``env`` parameter**: build-time ``GOOS``/``GOARCH``/``CGO_ENABLED`` come
through reemote's ``op(env=...)`` meta-parameter, and the build descriptor wraps
its whole step (``mkdir -p`` of the output's parent, ``cd`` into the module,
``go build``) in one ``sh -c`` compound so that prefix reaches ``go build`` and
not just the leading ``mkdir`` -- the log line reads
``env CGO_ENABLED=0 sh -c '...'``. And a caller switching **target platforms**
passes ``force=True`` rather than expecting a settings comparison:
``go version -m`` records the target platform only when the build injected it,
so the operation cannot see the previous request's ``GOOS``.

No request flags ``sudo``: the module and the binary live under ``/tmp``,
writable by the connecting user, who is also the one whose go caches the build
reads. Like the other examples, every request carries ``when=is_ubuntu``: the
shared ``inventory.json`` holds one host in the ``"Ubuntu"`` family alongside
hosts in other families, so each run builds on the Ubuntu host and skips every
other one -- plan and log render ``[skipped: when guard is_ubuntu]`` for the
rest. The guard is not cosmetic: the ``go build`` shell is not self-guarding, so
on an unprovisioned host it would fail with ``CommandNotFoundError``. go is
*not* installed by default on the demo host, so ``main`` first awaits
``provision_go`` from the sibling ``provision_go.py``, which installs the
``golang-go`` deb with ``apt.packages`` (idempotent, so the run is cheap on a
host that already has it).

Run it with::

    uv run python examples/modules/go/go_build.py
"""

import asyncio
import io
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
    files,
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

# The throwaway module to build, under /tmp so no request needs root. The two
# source streams are module-level ``io.BytesIO`` constants: a transfer's IO
# source must be seekable and identity-stable across the prepare and apply
# phases (``OperationRequest`` compares streams by identity, not content).
MODULE_DIR = "/tmp/reemote-go-build-demo"
GO_MOD_SRC = io.BytesIO(b"module example.com/hello\n\ngo 1.21\n")
MAIN_GO_SRC = io.BytesIO(
    b"package main\n"
    b"\n"
    b'import "fmt"\n'
    b"\n"
    b"func main() {\n"
    b'\tfmt.Println("hello from reemote")\n'
    b"}\n"
)
# The build writes into a ``bin`` subdirectory; ``go.build`` mkdirs the output's
# parent itself, and the whole tree goes away in the final cleanup request.
BINARY_PATH = f"{MODULE_DIR}/bin/hello"

# Build-time environment through the ``op(env=...)`` meta-parameter (there is
# no per-operation ``env`` keyword): a static tuple of ``(key, value)`` pairs,
# hashable and identical in both phases, rendered by the driver as an
# ``env CGO_ENABLED=0`` prefix around the whole ``sh -c``-wrapped build.
BUILD_ENV = (("CGO_ENABLED", "0"),)


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows so
    both deploy phases reach the same eligibility decision and the determinism
    guard holds. The Ubuntu demo host is the one ``provision_go.py`` installs
    the go toolchain on before this example deploys (see the module docstring),
    so the build is guarded to that family; on a host outside it the bare
    ``go build`` shell would fail before doing anything, so the driver skips
    every request instead.
    """
    return host.family == "Ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Stage the module, build it -- twice -- then remove the tree.

    Branch-free, so the request sequence is identical in the prepare and apply
    phases (a branch on a mutable fact would raise DeterminismError). The first
    three requests stage the demo module with the files operations; the fourth
    builds it, and the fifth carries the same arguments and is a no-op in the
    apply phase once the binary's embedded compiler matches the toolchain
    (the fresh per-step fact cache reads ``go version -m`` back). The last
    request removes ``MODULE_DIR`` recursively -- binary and sources alike --
    leaving the host as found so the shared-host example sweep is not polluted.

    No request is elevated: everything lives under the connecting user's
    ``/tmp``, and ``sudo`` would only build the binary as root.
    """
    yield op(
        files.directory,
        name="Stage the demo module",
        path=MODULE_DIR,
        when=is_ubuntu,
    )
    yield op(
        files.put,
        name="Upload go.mod",
        src=GO_MOD_SRC,
        dest=f"{MODULE_DIR}/go.mod",
        when=is_ubuntu,
    )
    yield op(
        files.put,
        name="Upload main.go",
        src=MAIN_GO_SRC,
        dest=f"{MODULE_DIR}/main.go",
        when=is_ubuntu,
    )
    yield op(
        go.build,
        name="Build the hello binary",
        src_dir=MODULE_DIR,
        output_path=BINARY_PATH,
        env=BUILD_ENV,
        when=is_ubuntu,
    )
    yield op(
        go.build,
        name="Re-run the build (no-op)",
        src_dir=MODULE_DIR,
        output_path=BINARY_PATH,
        env=BUILD_ENV,
        when=is_ubuntu,
    )
    yield op(
        files.directory,
        name="Remove the demo tree",
        path=MODULE_DIR,
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
        # shells out to go, so the example runs even on a host where go was
        # never installed (see provision_go.py).
        print("\n== Provisioning golang-go (provision_go.py) ==")
        await provision_go(inventory)

        # --dry: prepare phase only. The build's fact reads (GoVersion and the
        # binary's GoBinaryBuildInfo) are gathered over SSH -- both are
        # read-only -- so the plan reflects each host's real pre-deployment
        # state; nothing that mutates a host is sent. A request the ``when``
        # guard rejects is planned as [skipped: when guard is_ubuntu] without
        # materializing it.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("\n== Plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run: prepare, then apply in lockstep across hosts over SSH. The
        # staging requests land first, the build compiles into /tmp, and the
        # re-run reports the apply-phase no-op against the fresh binary.
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
        # binary surfaces as CommandNotFoundError, a CommandError subclass).
        # Print that message -- no traceback -- and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
