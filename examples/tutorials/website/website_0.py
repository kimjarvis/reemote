# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: the basic two-phase model -- read a fact, then create a file.

This is step 0 of the website tutorial (``docs/articles/website.md``): the
smallest complete reemote program, demonstrating the two ideas the rest of the
series builds on.

* **Facts read the machine.** ``gather_fact`` asks a host what it already looks
  like -- here ``LinuxName`` reports the distribution -- and changes nothing.
* **Operations change the machine.** A *deploy* is a generator that yields
  operation *requests* built by ``op(...)``. ``run`` drives it in two phases:
  **prepare** gathers facts and builds a plan without sending a single mutating
  command, then **apply** re-reads the facts and sends only the commands the
  plan calls for.

The target is a local container reached through the **docker connector**, so the
example needs no SSH and no cloud. Start it once with::

    podman run -d --name beta ubuntu:24.04 tail -f /dev/null

``identifier`` is the container name ``beta``. Because that container already
exists, the connector attaches to it and leaves it running, so its state persists
between runs -- which is what makes **idempotence** visible. The script drives the
same deploy twice: on a fresh ``beta`` the first apply creates ``/tmp/hello.txt``
and reports ``[changed]``, and the second finds it already there and reports
``[no change]``. Run the script again and both lines read ``[no change]`` -- a
converged host is left alone, which is the whole point.

``run`` returns ``(plans, report, hosts)``: ``format_plan`` renders the plan (one
line per host per operation, with the reason), and ``format_log`` renders the
apply outcome, appending the ``$`` command line for every operation that actually
changed something. A ``dry=True`` run would stop after prepare and return
``report=None``; this example applies for real.

``gather_fact`` opens connections but does not close them, so the fact hosts are
closed here; ``run`` builds and closes its own hosts. Both are coroutines, so
``main`` is async and driven by ``asyncio.run``.

Run it with::

    uv run python examples/tutorials/website/website_0.py
"""

import asyncio
import sys

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    LinuxName,
    files,
    format_log,
    format_plan,
    gather_fact,
    op,
    run,
)

# The website tutorial's local target: a container named ``beta``, reached
# through the docker connector (``docker_cmd`` selects podman). ``identifier``
# names the already-running container, so its state persists between runs -- see
# the module docstring for the one-line ``podman run`` that starts it.
INVENTORY = [
    {
        "host": "beta",
        "connection": {
            "connector": "docker",
            "identifier": "beta",
            "docker_cmd": "podman",
        },
    },
]


def deploy(host: Host):
    """Create ``/tmp/hello.txt``: a single operation request.

    A deploy is a generator -- each ``op(...)`` records *what* to do (the
    operation ``files.file`` plus its arguments) and run metadata (``name``),
    while ``run`` decides *when* and *whether* to send a command. ``op`` sends
    nothing itself, so the very same generator is safe to drive in both phases.
    """
    yield op(files.file, name="create hello.txt", path="/tmp/hello.txt", present=True)


async def main() -> None:
    try:
        # -- Facts read the machine ----------------------------------------
        # gather_fact fans LinuxName.process out across the hosts and returns a
        # host-name-keyed mapping. It opens connections but changes nothing, and
        # leaves the connections open -- so close them here.
        fact_hosts = [Host.from_inventory(entry) for entry in INVENTORY]
        try:
            distros = await gather_fact(fact_hosts, LinuxName)
        finally:
            await asyncio.gather(*(host.aclose() for host in fact_hosts))
        print("== facts (read-only) ==")
        for name, distro in distros.items():
            print(f"{name}: {distro}")

        # -- Operations change the machine, in two phases -------------------
        # Drive the same deploy twice against the persistent beta container: the
        # first apply creates the file, the second finds it already there. That
        # is idempotence -- re-running converges instead of repeating work.
        for attempt in (1, 2):
            plans, report, _ = await run(INVENTORY, deploy)
            print(f"\n== run {attempt}: plan (prepare) ==")
            print(format_plan(plans))
            if report is not None:
                print(f"== run {attempt}: log (apply) ==")
                print(format_log(report))
    except (CommandError, HostConnectionError, InventoryError) as exc:
        # The library reports an unreachable host (is beta running?), a failed
        # command, or a malformed inventory as a typed error whose message names
        # the cause -- print it without a traceback and exit non-zero.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
