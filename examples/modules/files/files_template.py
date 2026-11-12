# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: render a control-side Jinja2 template with files.template.

This is a *usage* example, not part of the library. ``files.template`` is
``files.install`` with a render step in front: instead of a literal ``content``
string it takes a Jinja2 *template* -- a seekable IO-like **stream** on the
control machine (here a module-level ``io.StringIO``), never a path -- plus a
``variables`` mapping, expands the template on the control machine, and then
spool-and-installs the rendered text exactly as ``files.install`` does. It is
the answer to "write this config, but fill in a per-host value" -- the task a
literal ``files.install`` content cannot express without building the string
by hand in the deploy body.

The template rides the ``files.put`` stream contract. The stream must be
*seekable*: it is rewound to position 0 before every read, so the prepare-phase
render and the apply-phase render see the same text. And because
``OperationRequest`` equality compares a stream *by identity* -- an ``IO``
object hashes by identity too, so it satisfies the request hashability rule --
the deploy must pass the *same* object in both phases, hence the module-level
constant rather than a fresh ``StringIO`` inside the generator (a new object
per phase would trip the determinism guard). One shared stream serves every
host: only the ``variables`` differ per machine, not the template text.

The write half is identical to ``files.install`` (see
``examples/modules/files/files_install.py``), because ``files.template``
delegates to it:

1. Idempotence: the *rendered* text is compared against the destination's
   current content with the ``FileContents`` fact. When they already match,
   nothing is yielded and the request reports no change. Idempotence stays
   content-only -- a ``mode``/owner drift does not re-install.
2. Spool: an ``InstallSpoolCommand`` transfer copies the rendered content over
   scp to a dest-derived ``/tmp`` spool file -- unprivileged regardless of the
   request's ``sudo``.
3. Install: an ``InstallCommand`` shell runs ``mkdir -p``/``cp``/optional
   ``chown``/``chmod``/``rm -rf`` wrapped in ``sh -c``, so ``sudo=True``
   elevates every step and the file lands root-owned (or ``user``/``group``)
   under ``/etc``.

Rendering uses Jinja2 with ``StrictUndefined``, so a variable the template
references but ``variables`` omits raises ``ValueError`` before anything
reaches the host -- a config that silently drops a value is worse than a
deploy that stops. A template's trailing newline is preserved
(``keep_trailing_newline``), so the rendered text compares cleanly against a
destination that keeps it.

``variables`` is a mapping, but a request argument must stay *hashable*
(``compose(dedupe=True)`` hashes every value), so at the ``op()`` call site it
is spelled as a tuple of ``(key, value)`` pairs -- never a dict literal, which
``OperationRequest`` would reject with ``UnhashableArgumentError``. The pairs
may be built from the ``host`` the deploy receives, which is what makes the
rendering per-host: ``host.name`` is static inventory data, so the prepare and
apply phases render the identical string and the determinism guard holds.

The script installs a per-host rendered config, then runs the very same deploy
a second time: the first run writes, the second reports no change on every
host (the rendered content now reads back equal).

Every request carries ``when=is_centos``, the declarative eligibility guard:
the predicate reads only **static** inventory data (``host.group``) and is a
stable module-level function, so both phases reach the same skip decision and
hosts outside the ``"centos"`` group render the step as
``[skipped: when guard is_centos]`` in their lockstep slot.

The path is resolved from ``__file__``, so the example works from any working
directory; point ``INVENTORY_PATH`` at your own file (or edit
``inventory.json``) to target your machines.

Run it with::

    uv run python examples/modules/files/files_template.py
"""

import asyncio
import io
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
    files,
    format_log,
    format_plan,
    op,
    run,
    validate_inventory,
)

# The root-owned destination and the Jinja2 template text installed there.
# The template is a stream, created once on the control machine: the same
# object is passed to every render in every phase, so the prepare and apply
# request sequences compare equal (OperationRequest compares a stream by
# identity). It is rewound before each read, so one stream serves every host.
DEST = "/etc/reemote-template-demo.conf"
TEMPLATE_TEXT = """\
# Managed by reemote files.template -- do not edit by hand.
[server]
hostname = {{ hostname }}
listen_port = {{ port }}
{% if enable_tls %}
tls = on
{% else %}
tls = off
{% endif %}
"""
TEMPLATE_STREAM = io.StringIO(TEMPLATE_TEXT)

# examples/modules/files/files_template.py -> the project root, so the inventory
# is found whatever the current working directory is.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = PROJECT_ROOT / "inventory.json"


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read an inventory from a JSON file and validate it.

    The file must hold a JSON array of inventory entries -- dictionaries
    each carrying at least ``host`` and ``connection``. Returns the parsed
    list, ready to materialize into hosts; raises ``InventoryError`` (via
    ``validate_inventory``) when the document is not an array or an entry
    does not match the schema.
    """
    inventory = json.loads(path.read_text(encoding="utf-8"))
    validate_inventory(inventory)
    return inventory


def is_centos(host: Host) -> bool:
    """``when`` guard: the host belongs to the inventory group ``"centos"``.

    Reads only static inventory data (``host.group``, set by the ``"group"``
    key of the inventory entry), never a mutable fact, and stays a stable
    module-level function -- the two rules every ``when`` predicate follows
    so both deploy phases reach the same eligibility decision.
    """
    return host.group == "centos"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Render a per-host config and install it at a root-owned path.

    ``variables`` is spelled as a tuple of ``(key, value)`` pairs -- the
    hashable form a request argument must take -- and one value comes straight
    from the ``host`` (``host.name``), so each machine renders its own
    hostname into the very same shared template stream. ``sudo=True`` elevates
    only the install half; the scp spool transfer always rides as the login
    user. The mode defaults to "0644"; pass ``mode="0600"`` for a
    secret-bearing config, or ``user``/``group`` to land it under a service
    account.
    """
    yield op(
        files.template,
        name="Install rendered config",
        template=TEMPLATE_STREAM,
        dest=DEST,
        variables=(("hostname", host.name), ("port", 8080), ("enable_tls", True)),
        sudo=True,
        when=is_centos,
    )


async def main() -> None:
    try:
        inventory = load_inventory(INVENTORY_PATH)
        print(f"== Inventory loaded from {INVENTORY_PATH} ==")
        print(", ".join(entry["host"] for entry in inventory))

        # Dry run: the plan materializes against pre-deployment state.
        plans, _, _hosts = await run(inventory, deploy, dry=True)
        print("== Plan: install rendered config (--dry) ==")
        print(format_plan(plans))

        # First real run: render, spool + install on every host whose content
        # differs.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Log: install rendered config (first run) ==")
        assert report is not None
        print(format_log(report))

        # Second run of the SAME deploy: FileContents now reads back the
        # rendered text the first run installed, so every request reports no
        # change -- the per-host render is deterministic across runs.
        plans, report, _hosts = await run(inventory, deploy)
        print("\n== Log: install rendered config (second run, no-op) ==")
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
