# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: typed role variables — ``resolve_vars`` precedence, tuples, typo gates.

A migrated role declares a frozen pydantic ``<Role>Vars`` model with
**unprefixed** fields and reads ``hv = v.for_host(host)`` in its body instead
of spelunking ``host.extra`` (§8). ``resolve_vars(cls, prefix, inventory,
overrides=...)`` resolves one model per host with a strict three-level
precedence — the model's defaults, then the inventory's ``prefix``-stripped
``extra`` keys, then call-site overrides — and a prefixed key that names no
field raises ``RoleVarsError`` instead of silently defaulting (§8.2): a
misspelled variable must never leave a role running on a value the operator
meant to override.

The traps this file runs for real:

- ``list`` never reaches an ``op(...)``: an inventory JSON array binds onto a
  ``tuple[str, ...]`` field and pydantic *coerces* it, which is what keeps the
  request hashable (``UnhashableArgumentError`` is the alternative);
- a prefixed typo (``demo_pot``) raises ``RoleVarsError``, and its message
  names every valid field — while an *unprefixed* key is simply left for the
  raw ``host.extra`` channel;
- a bad *value* for a good field name raises pydantic's ``ValidationError``,
  a different gate with a different fix.

Runs **entirely in memory**; the project root is added to ``sys.path`` to
import the test framework's ``SimulatedHost``.

Run it with::

    python examples/demonstrations/waves/vars_typed.py
"""

import asyncio
import sys
from collections.abc import Generator
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from reemote import Host, dnf, files, op, run_waves
from reemote.modules.server import LinuxDistribution
from reemote.rolevars import RoleVarsError, VarsSet, resolve_vars
from reemote.stages import StageItem, fact

# examples/demonstrations/waves/vars_typed.py -> the project root, so
# ``tests`` is importable (running a script by path puts its own folder, not
# the root, on sys.path).
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.simulated_host import SimulatedHost

INVENTORY = [
    {
        "host": "web1",
        "family": "RedHat",
        "connection": {"host": "web1.example"},
        # Only the bare key is a typo; ``unprefixed_key`` does not start with
        # ``demo_``, so it belongs to the raw host.extra channel and binds to
        # nothing here.
        "unprefixed_key": "not a role variable",
    },
    {
        "host": "web2",
        "family": "RedHat",
        "connection": {"host": "web2.example"},
        # A JSON *array* onto a tuple field: pydantic coerces, the request
        # stays hashable.
        "demo_packages": ["bash", "curl"],
        "demo_port": 9443,
    },
]


class DemoVars(BaseModel):
    """The role's variables: frozen, forbidding unknown fields, tuple sequences."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    packages: tuple[str, ...] = ("tar",)
    port: int = 8080


def deploy(host: Host, v: VarsSet[DemoVars]) -> Generator[StageItem]:
    """Read ``hv = v.for_host(host)`` — never ``host.extra`` by name.

    The resolved model is the role's *only* view of configuration: the
    defaults for web1, the inventory's prefixed keys for web2, the call-site
    overrides on top of both. The ``fact`` barrier after the first stage keeps
    the stream wave-shaped, like any real role body.
    """
    hv = v.for_host(host)
    yield op(
        dnf.packages,
        name="Install role packages",
        packages=hv.packages,
        sudo=True,
    )
    yield fact(LinuxDistribution, name="Which distribution")
    yield op(
        files.directory,
        name=f"Ensure port dir {hv.port}",
        path=f"/srv/demo-{hv.port}",
    )


def factory(entry: dict) -> SimulatedHost:
    """``host_factory``: plain simulated hosts, no seeding needed."""
    return SimulatedHost.from_inventory(entry)


async def main() -> None:
    # Precedence, printed: defaults -> inventory extra -> call-site overrides.
    v = resolve_vars(
        DemoVars, "demo_", INVENTORY, overrides={"packages": ("git",)}
    )
    for name, model in v.values.items():
        print(f"{name}: packages={model.packages} port={model.port}")
    # web1 keeps the model default (tuple-coerced override wins for packages),
    # web2's inventory list was coerced then overridden by the call-site tuple.

    report, _hosts = await run_waves(
        INVENTORY, lambda host: deploy(host, v), host_factory=factory
    )
    assert report is not None
    print("\n== Wave tree: one body, two variable sets ==")
    print(report.tree())

    # The typo gate: a prefixed key that names no field is a spelling mistake,
    # reported with every valid name — not silently dropped.
    bad = [
        {
            "host": "web1",
            "family": "RedHat",
            "connection": {"host": "web1.example"},
            "demo_pot": 1234,
        }
    ]
    try:
        resolve_vars(DemoVars, "demo_", bad)
    except RoleVarsError as exc:
        print(f"\ntypo gate: {exc}")

    # The value gate: a real field with an impossible value is pydantic's
    # ValidationError, not RoleVarsError — a different mistake, a different
    # message, the same fail-before-any-host-is-touched guarantee.
    try:
        resolve_vars(DemoVars, "demo_", INVENTORY, overrides={"port": "not-a-number"})
    except ValidationError as exc:
        first = exc.errors()[0]
        print(f"value gate: {first['loc'][0]}: {first['msg']}")


if __name__ == "__main__":
    asyncio.run(main())
