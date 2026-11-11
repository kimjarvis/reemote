# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Drop a dnf repository's cached metadata, scoped to that repo alone.

This is a *usage* example, not part of the library. It shows the
ansible-datadog role's metadata-clean step end to end over a **real SSH
transport** (asyncssh): ensure the repository file with ``dnf.repo``, then
drop its cached metadata with
``dnf.clean(what="metadata", repos=("DockerCE",))`` -- the scoped form of
the role's ``dnf clean metadata --disablerepo="*" --enablerepo=datadog``
command.

Why a dedicated operation: a repo whose *content* moved (the role's case
is an agent major-version downgrade) keeps serving stale cached metadata
until it is explicitly dropped -- dnf's own expiry will not fix a
downgrade. ``dnf.packages(clean=True)`` cannot express this: it is an
unscoped ``dnf clean all`` over every repo's metadata *and* cached
packages. ``dnf.clean`` removes one cache kind (``all``, ``metadata``,
``packages``, ``dbcache`` or ``expire-cache``; anything else raises
``ValueError``), and with ``repos`` set it touches no other repo's cache:
the rendering is
``dnf clean metadata '--disablerepo=*' --enablerepo=DockerCE`` (the disable
flag is shell-quoted whole so the remote shell never globs the ``*``).
dnf re-fetches the metadata on next use; pair this with ``dnf.makecache``
(see ``dnf_makecache.py``) to fetch it immediately, as the role does.

``dnf.clean`` is **non-idempotent** like ``dnf.update``: it reads no fact,
always reports a change and always runs -- the command is itself cheap
when the cache is already empty. The Ansible role gates the clean on the
repo task's ``changed`` flag; reemote cannot gate on a same-deploy write
(a ``when`` predicate must reach the same decision in both phases, and the
repo-file fact flips between prepare and apply), so the clean is
unconditional and sits right after the ``dnf.repo`` request in the same
deploy. The role also marks its step ``failed_when: false``; reemote
keeps its fail-fast convention -- a failing clean raises ``CommandError``
at apply, which ``main`` reports without a traceback.

Both requests carry ``when=is_centos`` (static inventory data only) and
``sudo=True``: writing the repo file and removing cached data from the
shared cache need root. The script reads the shared project-root
``inventory.json`` and validates it up front, runs the prepare phase
(``dry=True``) to print the plan, then the real run -- the same two-phase
shape as ``dnf_repo.py``, whose plain Docker CE arguments it reuses so the
shared CentOS hosts converge on one repo-file content.

Run it with::

    uv run python examples/modules/dnf/dnf_clean.py
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
    dnf,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# The Docker CE repository -- identical arguments to dnf_repo.py's, so every
# dnf example converges on the same /etc/yum.repos.d/DockerCE.repo content.
DOCKER_REPO = {
    "src": "DockerCE",
    "description": "Docker CE Stable",
    "baseurl": "https://download.docker.com/linux/centos/$releasever/$basearch/stable",
    "gpgkey": "https://download.docker.com/linux/centos/gpg",
}

# examples/modules/dnf/dnf_clean.py -> the project root, so the inventory is
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

    Reads only static inventory data (``host.group``), never a mutable fact,
    and stays a stable module-level function -- the two rules every ``when``
    predicate follows so both deploy phases reach the same eligibility
    decision. ``dnf`` only exists on the CentOS hosts.
    """
    return host.group == "centos"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Ensure the repo file, then drop only that repo's cached metadata.

    Branch-free: the request sequence is identical in both phases and on
    every host; eligibility rides the ``when=is_centos`` guard. The repo
    write is content-idempotent (a no-op once the file matches), while the
    scoped clean is stateless and runs on every deploy -- the
    unconditional-refresh design, since gating on the same-deploy write
    would not be determinism-safe. ``repos`` is a tuple because
    ``OperationRequest`` kwargs must stay hashable.
    """
    yield op(
        dnf.repo,
        name="Ensure Docker CE repo",
        sudo=True,
        when=is_centos,
        **DOCKER_REPO,
    )
    yield op(
        dnf.clean,
        name="Drop Docker CE metadata",
        what="metadata",
        repos=("DockerCE",),
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Facts are gathered over SSH (read-only)
        # so the plan reflects real pre-deployment state; nothing that
        # mutates a host is sent. The stateless clean plans as a change on
        # every CentOS host, every run.
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
        # The document parsed but did not match the inventory schema.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except (CommandError, HostConnectionError, PrivilegeError) as exc:
        # Typed host-level failure: print the message, no traceback, and
        # exit non-zero so the failure stays script-visible.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())