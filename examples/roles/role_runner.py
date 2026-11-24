# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Shared command-line runner for the migrated roles (§17.3).

Every thin runner under ``examples/roles/`` is a ~10-line script that imports
one role and hands its factory, vars model and variable prefix to ``role_main``
here::

    # examples/roles/php.py -- a thin runner
    import asyncio
    from pathlib import Path

    from reemote.roles import PhpVars, php

    from role_runner import role_main

    if __name__ == "__main__":
        asyncio.run(role_main(php, PhpVars, "php_", Path("inventory.json")))

A runner may also pass call-site ``overrides`` -- role variables keyed by the
model's UNPREFIXED field names that win over the inventory's prefixed keys for
every host -- as ``examples/roles/apache.py`` does.

This module holds what the fourteen example scripts used to duplicate: load and
validate the inventory, resolve the role's variables, build the deploy (awaiting
an ``async def`` factory exactly once, §16.3), run one ``run_waves`` pass, print
the wave tree, and map every typed failure to a one-line ``ERROR:`` message and
a non-zero exit. ``run_waves`` owns the host connections, so there is no
per-host ``aclose`` and no dry-then-real double ``run()`` (§17.2).

It owns the ``--dry`` and ``--inventory`` flags. A dry run is a one-wave preview
(§6.3): ``run_waves(dry=True)`` stops at the first barrier.

``role_runner.py`` is imported by the thin runners by bare name (each runner
prepends its own directory to ``sys.path``), so the basename is globally unique
within ``examples/`` (§17.3). It is a helper, not an example: ``run_examples.py``
and ``tests/test_examples.py`` both skip it -- it exposes no ``host`` entrypoint
of its own and builds no requests, so sweeping it would only run an empty
program.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from reemote import (
    CommandError,
    Deploy,
    HostConnectionError,
    InventoryError,
    PrivilegeError,
    VarsSet,
    resolve_vars,
    run_waves,
    validate_inventory,
)

# The failures a live role run surfaces to the console: a bad inventory
# document, a transport/command/privilege error on some host, or an invalid
# role variable. Each is reported as one line, no traceback; the exit status is
# the signal. ``json.JSONDecodeError`` needs no clause -- it is a ValueError.
_ROLE_ERRORS = (
    InventoryError,
    CommandError,
    HostConnectionError,
    PrivilegeError,
    OSError,
    ValueError,
    TypeError,
)


def _parse_args(default_inventory: Path) -> argparse.Namespace:
    """Parse ``--inventory`` and ``--dry`` for a thin role runner."""
    parser = argparse.ArgumentParser(
        description="Run a reemote role against an inventory.",
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=default_inventory,
        metavar="FILE",
        help="path to the inventory JSON document (default: %(default)s)",
    )
    parser.add_argument(
        "--dry",
        action="store_true",
        help="preview one wave deep without touching a host",
    )
    return parser.parse_args()


async def role_main(
    factory: Callable[..., Deploy | Awaitable[Deploy]],
    vars_cls: type[BaseModel],
    prefix: str,
    inventory_path: Path = Path("inventory.json"),
    overrides: Mapping[str, Any] | None = None,
) -> None:
    """Load the inventory, resolve the role's variables, and run one wave pass.

    ``factory`` is the role's deploy factory: it takes a ``VarsSet[vars_cls]``
    and returns a ``Deploy`` (a ``host -> generator`` callable); a ``prefix``
    binds the inventory's role keys (``apache_`` -> ``ApacheVars``). An
    ``async def`` factory is awaited once here, before ``run_waves``, so its
    inventory-wide control-side IO runs a single time (§16.3). ``run_waves``
    owns the hosts and closes them itself (§17.2). Any typed role failure is
    printed as a one-line ``ERROR:`` and exits non-zero.

    ``overrides`` are call-site role variables -- keyed by the model's
    UNPREFIXED field names, unlike the inventory's ``prefix``-bound keys --
    applied uniformly to every host and taking precedence over both the model
    defaults and the inventory (see :func:`~reemote.rolevars.resolve_vars`).
    """
    args = _parse_args(inventory_path)
    try:
        inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
        validate_inventory(inventory)
        vars_set: VarsSet[Any] = resolve_vars(
            vars_cls, prefix, inventory, overrides=overrides
        )
        built = factory(vars_set)
        deploy: Deploy = await built if asyncio.iscoroutinefunction(factory) else built
        report, _hosts = await run_waves(inventory, deploy, dry=args.dry)
        print(report.tree())
    except _ROLE_ERRORS as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
