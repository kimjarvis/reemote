# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: manage an LXD image alias on real Ubuntu hosts.

This is a *usage* example, not part of the library. It drives reemote's two-phase
execution model end to end over a **real SSH transport** (asyncssh): the inventory is
read from ``inventory.json`` in the current working directory and validated up front
with ``validate_inventory``, so a malformed file fails with an ``InventoryError`` naming
the offending entry before any host is materialized or connected.

``lxd.image_alias`` reads the ``LxdImageAliases`` fact: an alias already
targeting ``fingerprint`` is a no-op, one pointing elsewhere is REPOINTED
(delete then create), an absent one is created, and ``present=False``
deletes it. Creating or repointing without a ``fingerprint`` raises
``ValueError`` -- and the daemon rejects an *unknown* fingerprint at
apply time, so a placeholder never works: the target must be the real
fingerprint of a cached image.

That fingerprint is a mutable fact, and a deploy generator runs twice
(prepare against pre-deployment state, apply against live state), so
reading it inside the generator is the phase-dependent argument the
determinism guard forbids. The script uses the sanctioned gather ->
snapshot -> factory split (the shape ``two_deploy_split.py`` teaches):

1. Run 1 caches the image (``lxd.image``, branch-free): afterwards an
   image aliased ``ubuntu/24.04`` is guaranteed on every eligible host.
2. A fact SNAPSHOT between the runs: ``gather_fact`` fans the
   ``LxdImages`` read out across the ubuntu hosts and the script freezes
   each host's ``ubuntu/24.04`` fingerprint into a plain dict.
3. ``build_alias_image(fingerprints)`` -- a factory closing over that
   frozen dict -- returns the alias deploy, built ONCE before both of
   its phases. Every request is flagged ``sudo=True``, so the ``lxc``
   client talks to the LXD daemon socket as root -- no password travels
   through operation arguments, plans or logs.

The alias deploy yields the same create request twice: the second is a
guaranteed no-op in the apply phase, because the first alias is visible
to it through the fresh per-step fact cache (in the prepare phase, where
nothing is sent, both plan as changes).

LXD is *not* set up out of the box on Ubuntu -- it ships as a snap, not a
default install -- so this example provisions it itself before deploying:
``main`` first awaits ``provision_lxd`` from the sibling ``provision_lxd.py``,
which installs the ``lxd`` snap with ``snap.package`` (pinned to the
``6/stable`` channel) and runs ``lxd init --auto``. Both steps are
idempotent -- the install on the ``SnapPackage`` fact, the init on the
daemon's own already-configured checks -- so the example runs even on a
host where LXD was never installed, and a re-run leaves an initialized
daemon untouched. The one prerequisite provisioning does not cover is
socket access for the unprivileged fact reads: the login user must be a
member of the ``lxd`` group (the demo Ubuntu host already is); the
operations themselves ride ``sudo=True`` and need no group membership.

Every request carries ``when=is_ubuntu``, the declarative eligibility guard: the
predicate reads only **static** inventory data -- ``host.family``, set by the
``"family"`` key of the inventory entry -- and is a stable module-level function, so the
prepare and apply phases reach the same skip decision. The driver skips a rejected
request without materializing it, so on hosts outside the ``"Ubuntu"`` family neither
the commands nor the facts behind them run, and plan and log render the step as
``[skipped: when guard is_ubuntu]`` in its lockstep slot. The snapshot is guarded the
same way: ``gather_fact`` takes no ``when`` parameter, so the script filters the host
list to the ``"Ubuntu"`` family before the fan-out. The inventory is read from
``inventory.json`` in the current working directory; edit it there to target your
machines.

Run it with::

    uv run python examples/modules/lxd/lxd_image_alias.py
"""

import asyncio
import json
import sys
from collections.abc import Callable, Generator
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
    gather_fact,
    lxd,
    op,
    run,
    validate_inventory,
)

# The LXD provisioning helper sits beside this script; add its directory
# to sys.path so ``import provision_lxd`` resolves whether the example is
# run by path or imported by the offline example harness.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from provision_lxd import provision_lxd

# The image run 1 caches, and the local alias the snapshot selects it by:
# the ubuntu: remote serves the container images, and lxd.image copies the
# reference under this alias (an already-cached image is a no-op).
CACHED_IMAGE = "ubuntu/24.04"
IMAGE_SOURCE = "ubuntu:24.04"

def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``, set by the ``"family"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision. It guards both
    the requests and (as a host-list filter) the ``LxdImages`` snapshot,
    keeping the ``lxc`` work off the hosts without LXD. The Ubuntu demo host
    is the one ``provision_lxd.py`` provisions with LXD before this example
    deploys (see the module docstring), so the deploy is guarded to that family.
    """
    return host.family == "Ubuntu"


def cache_image(host: Host) -> Generator[OperationRequest]:
    """Deploy 1: cache the image only, branch-free, one request per phase.

    Caching needs the LXD daemon socket, so the request is flagged
    ``sudo=True`` and rides under root.
    """
    yield op(
        lxd.image,
        name=f"Cache the {CACHED_IMAGE} image",
        image=CACHED_IMAGE,
        source=IMAGE_SOURCE,
        sudo=True,
        when=is_ubuntu,
    )


def build_alias_image(
    fingerprints: dict[str, str],
) -> Callable[[Host], Generator[OperationRequest]]:
    """Deploy 2 factory: close over the frozen post-cache fingerprints.

    The returned generator looks up ``fingerprints.get(host.name)`` -- a
    plain dict lookup on a snapshot taken BEFORE this deploy's prepare
    phase -- so both phases resolve the same fingerprint and the
    determinism guard holds. Reading the live ``LxdImages`` fact here
    instead would re-introduce the phase-dependent argument. The snapshot
    only covers the ubuntu hosts (the gather was filtered by
    ``is_ubuntu``), so the lookup returns None for the others and their
    request stream is empty; the requests also carry ``when=is_ubuntu``.
    """

    def alias_image(host: Host) -> Generator[OperationRequest]:
        fingerprint = fingerprints.get(host.name)
        if fingerprint is None:
            return
        yield op(
            lxd.image_alias,
            name="Point ubuntu-noble at the cached image",
            alias="ubuntu-noble",
            fingerprint=fingerprint,
            sudo=True,
            when=is_ubuntu,
        )
        yield op(
            lxd.image_alias,
            name="Point ubuntu-noble at the cached image (again)",
            alias="ubuntu-noble",
            fingerprint=fingerprint,
            sudo=True,
            when=is_ubuntu,
        )

    return alias_image


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
        print("== Inventory loaded from inventory.json ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Provision LXD (the snap + ``lxd init --auto``) on the Ubuntu hosts
        # before anything below reads or mutates LXD state, so the example
        # runs even on a host where LXD was never installed (see
        # provision_lxd.py).
        print("\n== Provisioning LXD (provision_lxd.py) ==")
        await provision_lxd(inventory)

        # Run 1: prepare + apply the image cache. Its apply-phase join is
        # the barrier guaranteeing every host finished before the snapshot
        # below. The when=is_ubuntu guard skips the non-Ubuntu hosts.
        plans, report, hosts = await run(inventory, cache_image)
        print("\n== Deploy 1: cache the image ==")
        print(format_plan(plans))
        assert report is not None
        print(format_log(report))

        # The snapshot: one concurrent read of the real post-cache image
        # store, keyed by host name. gather_fact has no ``when`` parameter,
        # so the is_ubuntu predicate filters the host list before the
        # fan-out -- the LxdImages read would fail on hosts without lxc.
        # run closed these hosts, but connect is lazy, so gather_fact
        # re-opens the eligible ones; close them all again when done.
        ubuntu_hosts = [host for host in hosts if is_ubuntu(host)]
        try:
            snapshot = await gather_fact(ubuntu_hosts, lxd.LxdImages)
        finally:
            for host in hosts:
                await host.aclose()

        # Freeze the fingerprint of the image run 1 guaranteed: the entry
        # carrying the CACHED_IMAGE alias. A plain str per host -- hashable
        # and value-comparable, so both phases build identical requests.
        fingerprints: dict[str, str] = {}
        for name, images in snapshot.items():
            entry = next(
                (
                    image
                    for image in images
                    if any(
                        alias.get("name") == CACHED_IMAGE
                        for alias in image.get("aliases") or []
                    )
                ),
                None,
            )
            assert entry is not None, (
                f"{name}: run 1 left no image aliased {CACHED_IMAGE!r}"
            )
            fingerprints[name] = entry["fingerprint"]
        print("\n== Post-cache snapshot (LxdImages) ==")
        for name, fingerprint in fingerprints.items():
            print(f"{name}: {CACHED_IMAGE} -> {fingerprint}")

        # Build deploy 2 ONCE, before either of its phases: both the dry
        # run and the real run use the very same callable closing over the
        # same frozen dict -- determinism by construction.
        alias_deploy = build_alias_image(fingerprints)

        # --dry for deploy 2: the plan is honest because the fingerprint
        # consults the post-cache snapshot, not pre-deployment state.
        plans, _, _hosts = await run(inventory, alias_deploy, dry=True)
        print("\n== Deploy 2 plan (prepare phase, --dry) ==")
        print(format_plan(plans))

        # Real run 2: point the alias at the cached image, twice.
        plans, report, _hosts = await run(inventory, alias_deploy)
        print("\n== Deploy 2 run log (apply phase) ==")
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