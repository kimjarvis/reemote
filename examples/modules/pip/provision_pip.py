# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Provision a pip virtualenv on the Ubuntu demo host before the pip examples run.

This is a *usage* example, not part of the library. It is the shared
prerequisite step for ``pip_packages.py``: reemote's ``pip`` module deliberately
dropped the baseline's ``virtualenv`` option (venv creation belongs to the
sibling ``pip.virtualenv``/``pip.venv`` operations, which a deploy expresses
as its own request), so a virtualenv is targeted by
passing its interpreter as ``pip=<venv>/bin/pip`` instead -- and this module
creates that virtualenv up front. It is also the documented way to install on
modern Ubuntu at all: the system interpreter is *externally managed* (PEP 668),
so a bare ``pip install`` there is refused, while a venv is always installable.

Provisioning is the reemote equivalent of two shell commands::

    sudo apt-get install -y python3-venv
    python3 -m venv --clear /tmp/reemote-pip-example-venv

The first is a real operation: ``install_python3_venv`` yields an
``apt.packages`` request (``packages=("python3-venv",)``, ``sudo=True``), driven
by ``run`` over the default ``Host.from_inventory`` factory, so it installs over
the real SSH transport and is idempotent on the ``AptPackages`` fact -- a host
that already has the wheel package plans no change. Ubuntu splits the stock
``python3-venv`` wheel copy out of the base ``python3`` package, and that wheel
is what ``venv``'s ``ensurepip`` bootstrap installs into the new environment;
without it ``python3 -m venv`` produces a pip-less venv (or fails outright).

The second deliberately stays a ``Shell`` *action* instead of a sibling
``pip.venv`` request -- the operation creates only when the path is absent,
while this helper needs ``--clear``'s always-fresh recreation -- and it rides
through ``gather_action``. Actions act immediately and cannot run inside a deploy
(prepare must never send, and the generator is re-invoked per phase), so
``provision_pip_venv`` awaits the ``apt.packages`` deploy to completion first
and only then fans the ``venv`` command out. ``--clear`` makes the creation
idempotent too: the path is replaced with a fresh environment on every run,
which also resets any package state a previous example left behind. The
command runs *unprivileged* (a /tmp venv belongs to the SSH user, and the
``pip`` commands the examples later send must run as that same user), and its
exit status is data, so a non-zero result is raised as a ``CommandError``
naming the host, while an unreachable host or a rejected sudo credential still
raises ``HostConnectionError`` / ``PrivilegeError``.

Both steps are scoped to the ``"Ubuntu"`` family:
``provision_pip_venv`` filters the inventory to that family before contacting
anything, so the other demo hosts are never touched.
``install_python3_venv`` also carries ``when=is_ubuntu`` -- the same static
guard the sibling example uses -- so the deploy is safe to drive against a
full inventory as well.

The module is importable (``from provision_pip import provision_pip_venv``)
and also runnable on its own to provision the host without running an
example::

    uv run python examples/modules/pip/provision_pip.py

The basename is deliberately not ``install.py`` or ``provision.py``: the
flatpak and server example directories already own those module names, and
the offline example harness imports every example in one process, where a
reused basename would resolve ``from ... import`` against the wrong
directory's cached helper.
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
    Shell,
    apt,
    gather_action,
    op,
    run,
    validate_inventory,
)

# The package that ships the stock ``ensurepip`` wheel on Ubuntu. A
# module-level tuple, so the request kwargs stay hashable and identical in
# the prepare and apply phases.
VENV_PACKAGES = ("python3-venv",)

# The example virtualenv: a fixed /tmp path, so every SSH user can create it
# without root and the examples can agree on the interpreter location without
# reading any host fact. ``--clear`` recreates it from scratch on every run.
VENV_PATH = "/tmp/reemote-pip-example-venv"
VENV_CREATE_COMMAND = f"python3 -m venv --clear {VENV_PATH}"



def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``), never a mutable fact,
    and stays a stable module-level function -- the two rules every ``when``
    predicate follows so both deploy phases reach the same eligibility
    decision and the determinism guard holds.
    """
    return host.family == "Ubuntu"


def install_python3_venv(host: Host) -> Generator[OperationRequest]:
    """Install the venv wheel package with ``apt.packages`` -- the first prerequisite.

    A branch-free deploy yielding one request, so the sequence is identical in
    the prepare and apply phases. ``apt.packages`` is idempotent on the
    ``AptPackages`` fact: a host that already has ``python3-venv`` plans no
    change. Installing writes system state, so the request is flagged
    ``sudo=True`` and the driver elevates the rendered
    ``apt-get -y ... install python3-venv``.
    """
    yield op(
        apt.packages,
        name="Install python3-venv",
        packages=VENV_PACKAGES,
        sudo=True,
        when=is_ubuntu,
    )


async def provision_pip_venv(inventory: list[dict[str, Any]]) -> None:
    """Provision the example virtualenv on the Ubuntu hosts, in order.

    The shared prerequisite the pip example awaits before its own logic.
    Scoped to the ``"Ubuntu"`` family: the inventory is filtered first, so no
    other demo host is materialized or contacted. Returns nothing and prints
    nothing -- it is a pure helper; the caller (an example's ``main`` or this
    module's) owns the output.

    Two barriers, in order:

    1. ``run`` drives ``install_python3_venv`` (an ``apt.packages`` deploy) to
       completion over the real SSH transport, closing its own hosts.
    2. ``gather_action`` fans the unprivileged ``Shell`` venv creation out
       across freshly built hosts and closes them when it finishes. It runs
       only after the deploy barrier, because an action cannot run inside a
       deploy.

    A non-zero venv-creation exit is raised as a ``CommandError`` naming the
    host (the action's exit status is otherwise data); an unreachable host or
    a rejected sudo credential raises ``HostConnectionError`` /
    ``PrivilegeError`` from either step. With no Ubuntu host in the inventory
    this is a no-op.
    """
    ubuntu = [entry for entry in inventory if entry.get("family") == "Ubuntu"]
    if not ubuntu:
        return

    # 1. Install the wheel package. ``run`` owns and closes the hosts it
    #    materializes.
    await run(ubuntu, install_python3_venv)

    # 2. Create the venv. ``gather_action`` re-connects (the deploy closed its
    #    hosts) and closes them again when it finishes, so build fresh ones.
    #    Unprivileged: the venv belongs to the SSH user, whose own ``pip``
    #    commands the examples later send must run as the same user.
    hosts = [Host.from_inventory(entry) for entry in ubuntu]
    results = await gather_action(hosts, Shell, command=VENV_CREATE_COMMAND)
    for name, result in results.items():
        if result["exit_status"] != 0:
            raise CommandError(
                name,
                VENV_CREATE_COMMAND,
                result["exit_status"],
                result["stderr"],
            )


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The file parsed, but an entry violates the inventory schema.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print("== Inventory loaded from inventory.json ==")
    print(", ".join(entry["host"] for entry in inventory))

    ubuntu = [entry["host"] for entry in inventory if entry.get("family") == "Ubuntu"]
    if not ubuntu:
        print("\n== No host in the 'Ubuntu' family; nothing to provision ==")
        return

    print(f"\n== Provisioning the pip example venv on: {', '.join(ubuntu)} ==")
    try:
        await provision_pip_venv(inventory)
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # The library reports every host-level failure as a typed error whose
        # message names the host and the failing command. Print that message
        # -- no traceback -- and exit non-zero so the failure stays visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"== venv ready at {VENV_PATH} ==")


if __name__ == "__main__":
    asyncio.run(main())
