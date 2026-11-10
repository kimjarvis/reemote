# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Ensure an apk repository line with ``apk.repository``.

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
it finishes. The path is resolved from ``__file__``, so the example works from
any working directory; point ``INVENTORY_PATH`` at your own file to target
your machines.

``run`` is a coroutine (both phases are async), so ``main`` is async and
driven by ``asyncio.run``.

``apk.repository`` is a reemote5 original modeled on ``apt.repo`` --
pyinfra's ``apk`` namespace ships no repository operation, so the
``ApkRepositories`` fact had no write counterpart. It ensures one line in
``/etc/apk/repositories``: the ``ApkRepositories`` fact is read first,
``src`` is parsed into the same ``url``/``tag`` entry shape, and an entry
that is already configured is a no-op. When an edit *is* due the managed
file is rewritten rather than patched in place -- the spool-and-install
pair ``apt.repo`` uses, not pyinfra's ``echo >>``/``sed -i``: the
``ApkRepositoriesFile`` content fact reads the file's current text, one
pure transform adds the line exactly once (``present=True``) or drops
every line the escaped pattern matches (``present=False``), an
unprivileged scp spools the result to a ``/tmp`` path named after the
managed file, and one elevated ``sh -c`` installs it over the protected
file with mode ``0644`` and removes the spool. Because the transform works
on the real content, the file's other lines and comments survive verbatim.
A tagged ``@testing URL`` line and the bare URL are *distinct* repositories
to the presence check; an unparseable ``src`` (a lone ``@tag``, several
URLs on one line) raises ``ValueError`` in the prepare phase, before
anything is sent.

The line ensured here is the ``community`` repository -- the source of
most everyday Alpine packages beyond the minimal ``main`` set, and the
proposal's motivating case (packages like libvirt live there). The URL
uses the CDN's ``latest-stable`` alias so the example does not hard-code a
release; **match it to your hosts first**: run the read-only
``apk_apk_repositories.py`` fact example to see the configured sources,
and pin ``COMMUNITY_URL`` to the hosts' release (``v3.19``, ``v3.20``, ...,
from ``/etc/alpine-release``) when they track a fixed one -- the presence
check compares whole entries, so a differently-versioned community line
already in the file does not count as this one, and mixing releases in one
file is asking for dependency trouble. If the exact line is already
configured, both requests below are no-ops and nothing is written.

The script yields the *same* request twice to show idempotence across the
two phases: the prepare phase plans both as changes when the line is
missing (nothing is sent in prepare, so both compare against the same
pre-deployment state), while the apply phase installs on the first and
reports the second as a no-op, because the fresh per-step fact cache now
sees the line the first install added. The removal direction is left to the
reader: ``present=False`` on the same ``src`` spools the file text without
the matching line -- the match is a *contains* match, so removing a bare
URL also drops a tagged line carrying it, and the same match gates the
append, so adding a line the file already carries writes nothing; gate
removals on the exact line when both variants are configured. Refreshing
the package index after a change is out of the operation's scope --
sequence an ``apk update`` (e.g. ``server.shell``) after it when needed.

Writing ``/etc/apk/repositories`` needs root, so the requests are flagged
``doas=True`` and the driver elevates the install command. ``doas`` rather
than ``sudo`` because the Alpine host has no ``sudo`` installed at all --
``sudo sh -c ...`` would die with ``sh: sudo: not found`` (exit 127).
Elevation reaches only the install, though: each request yields *two*
commands at different privilege levels -- the ``ApkRepoUploadCommand``
transfer rides the connection's scp channel as the *login user* (a
transfer never elevates, however the request is flagged), and the
``ApkRepoInstallCommand`` wraps its ``mkdir -p``/``cp``/``chmod``/``rm``
chain inside ``sh -c`` so every step, the spool cleanup included, runs
elevated. That asymmetry is exactly why the new text detours through
world-writable ``/tmp`` instead of being pushed straight into the
root-owned ``/etc/apk``. ``doas`` is passwordless here: reemote5 always runs
``doas -n``, which fails rather than prompts, so the host must grant the
login user a ``permit nopass`` rule in ``/etc/doas.conf`` (see
``examples/demonstrations/privilege/privilege_doas.py``); without one the
driver raises ``PrivilegeError``.

Every request also carries ``when=is_alpine``, the declarative eligibility
guard (see
``examples/demonstrations/composition/composed_when_guard.py``). Two rules
make it deterministic: the predicate reads only **static** inventory data --
``host.group``, set by the ``"group"`` key of the inventory entry -- never a
mutable fact, and it is a stable module-level function, so the prepare and
apply phases reach the same skip decision. ``when`` is a meta-parameter like
``doas``: captured on the request, never forwarded to the operation, and
skipped by the *driver*, so every host keeps the identical two-request
schedule (no ``if`` in the deploy body) and each request still consumes its
lockstep slot. On a host the guard rejects, the plan and the run log render
the step as ``[skipped: when guard is_alpine]`` and none of its commands
-- nor the ``ApkRepositories`` and ``ApkRepositoriesFile`` facts behind
them -- is materialized at all: the systemd hosts the shared inventory also
holds have no apk repositories file to manage.

Run it with::

    uv run python examples/modules/apk/apk_repository.py
"""

import asyncio
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
    apk,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# examples/modules/apk/apk_repository.py -> the project root, so the
# inventory is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"

# The repository line to ensure. The CDN's "latest-stable" alias avoids
# hard-coding a release; pin it to the hosts' Alpine release (v3.19, v3.20,
# ...) when they track a fixed one -- see the module docstring.
COMMUNITY_URL = "https://dl-cdn.alpinelinux.org/alpine/latest-stable/community"


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


def is_alpine(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"alpine"".

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision and the
    determinism guard holds. apk itself is the reason for the guard: a
    non-Alpine host has no ``/etc/apk/repositories`` to manage, so the
    driver skips every request instead.
    """
    return host.group == "alpine"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Ensure the community repository -- twice, to show idempotence.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError),
    and identical on every host: eligibility is expressed with the
    ``when=is_alpine`` guard each request carries, not with an ``if``. The
    first request installs the line if it is missing; the second carries
    the same arguments and is a guaranteed no-op in the apply phase,
    because the first install is already visible through the fresh per-step
    fact cache.

    The repositories file is root-owned, so both requests are flagged
    ``doas=True`` (the host has no ``sudo``): the driver elevates each
    request's install command over SSH with passwordless ``doas -n``, while
    its scp spool stays unprivileged.
    """
    yield op(
        apk.repository,
        name="Ensure the community repository",
        src=COMMUNITY_URL,
        present=True,
        doas=True,
        when=is_alpine,
    )
    yield op(
        apk.repository,
        name="Re-ensure the community repository (no-op)",
        src=COMMUNITY_URL,
        present=True,
        doas=True,
        when=is_alpine,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # --dry: prepare phase only. Facts are still gathered over SSH (read-only)
        # so the plan reflects each host's real pre-deployment state; no command
        # that mutates a host is sent. A request the ``when`` guard rejects is
        # planned as [skipped: when guard is_alpine] without materializing it,
        # so its fact is not read on that host either.
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