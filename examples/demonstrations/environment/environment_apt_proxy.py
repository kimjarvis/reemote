# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: reach an apt repository through a proxy with ``op(..., env=...)``.

This is a *usage* example, not part of the library. It shows the ``env``
meta-parameter -- an operation's counterpart of the ``env`` keyword the
``Shell``/``Script`` actions take, and the equivalent of Ansible's task-level
``environment:``. The scenario is the one a proxied host hits immediately: a
Zabbix agent has to be installed from a third-party repository, and neither the
repository refresh nor the install can reach the internet directly. Every
``apt`` command the deploy runs therefore needs the proxy variables in its own
environment.

``env`` is a *meta-parameter*, consumed by the driver and never forwarded to the
operation, so ``apt.packages`` keeps its own argument list untouched and the
variables apply to every command the operation yields. The value may be a
mapping or a sequence of ``(key, value)`` pairs; ``OperationRequest`` stores it
as a sorted tuple of pairs, which is what keeps a request carrying a plain dict
hashable -- and what makes the rendered text independent of the order the
mapping was built in. A name or value that is not a ``str`` is refused at the
call site, because the value is spliced into a shell word.

The prefix sits **innermost**, inside ``doas``/``sudo``/``su``:
``sudo env http_proxy=... apt-get install -y zabbix-agent``. That placement is
the whole point. `sudo` drops the caller's environment by policy, so a prefix
applied *outside* the escalation would be reset before apt ran; inside it, the
variables belong to the command that needs them.

One descriptor kind ignores ``env``, and the reason is structural rather than a
gap: ``apt.repo``'s sources-file write is a ``TransferCommand``, which rides the
scp channel instead of a shell, so there is no process whose environment could
be set. The example therefore shows the split clearly -- the upload line carries
no prefix, while the install and the ``apt-get update`` that follow it do.
``WaitCommand`` (a fact poll) likewise has no command environment.

The values appear in the log. ``format_log`` prints
``$ sudo env http_proxy=http://proxy.example:3128 ...``, identical to the text
sent to the transport: an ``env`` entry is part of the command, not a
credential, so it is reported like any other argument. (A password, which the
``privilege`` demonstrations supply from the inventory, is the opposite case and
never reaches the log.)

Unlike the ``privilege`` demonstrations, this one runs **entirely in memory** and
opens no SSH connection: it injects the test framework's ``SimulatedHost``
through ``run``'s ``host_factory``, so the plan, the run log and the final apt
state all come from the in-memory stand-in and the example is runnable anywhere.
The environment is inert there -- the simulated seam runs no shell -- but the
*rendering* is the same code the real transport goes through, which is what the
assertions below check. ``SimulatedHost`` is test-framework scaffolding (it lives
in ``tests/``, never in the library), so the project root is added to
``sys.path`` to import it.

The deploy is guarded with ``sub_deploy(..., when=is_ubuntu)``, since the
``apt`` work only makes sense on the Ubuntu host; the shared ``inventory.json``
also holds RedHat and Alpine hosts, which skip it without a fact read.

Run it with::

    uv run python examples/demonstrations/environment/environment_apt_proxy.py
"""

import asyncio
import json
import sys
from collections.abc import Generator
from pathlib import Path

from reemote import (
    Host,
    InventoryError,
    OperationRequest,
    apt,
    format_log,
    format_plan,
    op,
    run,
    sub_deploy,
    validate_inventory,
)

# examples/demonstrations/environment/environment_apt_proxy.py -> the project
# root, so ``tests`` is importable (running a script by path puts its own
# folder, not the root, on sys.path).
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.simulated_host import SimulatedHost

# The proxy the demo host has to reach every archive through. Written as a
# tuple of pairs rather than a dict: it is a module-level constant that ends up
# in a request, and a tuple is hashable without ``OperationRequest`` having to
# copy it. A dict would work identically at the call site.
APT_PROXY = (
    ("http_proxy", "http://proxy.example:3128"),
    ("https_proxy", "http://proxy.example:3128"),
    ("no_proxy", "localhost,127.0.0.1"),
)

ZABBIX_REPO = "deb http://repo.zabbix.com/zabbix/7.0/ubuntu noble main"


def is_ubuntu(host: Host) -> bool:
    """``when`` guard: the host belongs to the ``"Ubuntu"`` OS family.

    Reads only static inventory data (``host.family``), never a mutable fact, so
    the prepare and apply phases reach the same eligibility decision.
    """
    return host.family == "Ubuntu"


def deploy(host: Host) -> Generator[OperationRequest]:
    """Add the Zabbix repository and install the agent, both through the proxy.

    ``apt.repo`` is the spool-and-install write family, so one request yields
    three descriptors: the sources file uploaded to a ``/tmp`` spool (a
    transfer, no environment), the elevated install (a shell, so the prefix
    rides on it), and the index refresh ``update_cache=True`` appends after the
    write. ``apt.packages`` then installs the agent with the same environment,
    and the final request deliberately carries **no** ``env`` -- an unrelated
    package fetched from the already-cached archive needs no proxy, and its log
    line shows the difference.
    """
    yield op(
        apt.repo,
        name="Add the Zabbix repository",
        src=ZABBIX_REPO,
        filename="zabbix",
        update_cache=True,
        sudo=True,
        env=APT_PROXY,
    )
    yield op(
        apt.packages,
        name="Install zabbix-agent",
        packages=("zabbix-agent",),
        sudo=True,
        env=APT_PROXY,
    )
    yield op(
        apt.packages,
        name="Install curl",
        packages=("curl",),
        sudo=True,
    )


# The family guard, composed once at the outermost level: every request above
# runs only on an Ubuntu host, and a skipped request is never materialized, so
# its facts (``AptSources``, ``AptPackages``) are never read on the other hosts.
deploy = sub_deploy(deploy, when=is_ubuntu)


async def main() -> None:
    try:
        inventory = json.loads(Path("inventory.json").read_text(encoding="utf-8"))
        validate_inventory(inventory)
    except (OSError, json.JSONDecodeError) as exc:
        # The inventory file is missing, unreadable or not valid JSON. Nothing
        # is contacted -- the run is in memory -- so Python's own message is the
        # whole report.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except InventoryError as exc:
        # The document parsed but did not match the inventory schema.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print("== Inventory loaded from inventory.json ==")
    print(", ".join(f"{entry['host']} ({entry['family']})" for entry in inventory))

    def simulated_factory(entry: dict) -> SimulatedHost:
        """``host_factory`` building the in-memory stand-in for each host.

        A fresh host per ``run``, so the dry plan and the apply run both start
        from an empty apt state. The Ubuntu host is the only eligible one; the
        others idle at the lockstep barrier after their skip.
        """
        return SimulatedHost.from_inventory(entry)

    # --dry: prepare phase only. The plan names the operations, never the
    # commands, so an environment is not part of it.
    plans, _, _hosts = await run(
        inventory, deploy, host_factory=simulated_factory, dry=True
    )
    print("\n== Plan (prepare phase, --dry) ==")
    print(format_plan(plans))

    # Apply phase: each command goes through the same ``command_shell``
    # rendering the asyncssh transport would send, so the log below *is* the
    # wire text -- proxy variables included.
    _plans, report, hosts = await run(inventory, deploy, host_factory=simulated_factory)
    print("\n== Run log (apply phase) ==")
    assert report is not None
    print(format_log(report))

    log = format_log(report)
    # The request stores the pairs sorted, so the prefix follows that order.
    proxy = " ".join(f"{key}={value}" for key, value in sorted(APT_PROXY))
    # Only the Ubuntu host runs anything: its three proxied commands (the
    # spooled sources install, the index refresh, the agent install), the
    # sources upload that precedes them and the unproxied curl install. The
    # RedHat and Alpine hosts skip the deploy, so they contribute none.
    assert log.count("\n    $ ") == 5
    # Values are command text, so they are logged -- and they sit *inside* the
    # escalation, which is where sudo would not reset them.
    assert f"$ sudo env {proxy} apt-get update" in log
    assert sum(f"sudo env {proxy} " in line for line in log.splitlines()) == 3
    # The spool upload rides scp: the same request's transfer line carries no
    # prefix, because there is no process to give one to.
    assert "$ scp <apt sources>" in log and f"env {proxy} scp" not in log
    # The unguarded request proves the other direction -- no ``env``, no prefix.
    assert "$ sudo DEBIAN_FRONTEND=noninteractive" in log

    print("\n== Final simulated state ==")
    for host in hosts:
        assert isinstance(host, SimulatedHost)
        if host.family == "Ubuntu":
            print(
                f"{host.name}: sources={sorted(host.remote_files)} "
                f"apt_packages={sorted(host.apt_packages)}"
            )
        else:
            print(f"{host.name}: skipped (family={host.family!r})")


if __name__ == "__main__":
    asyncio.run(main())
