# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: inventory + nginx setup -- an ad-hoc action, then a converged base.

Step 1 of the website tutorial (``docs/articles/website.md``). With the basic
two-phase model in hand (``website_0.py``), this installs the shared nginx base
every web host needs and shows the two ways reemote touches a fleet:

* **The ``Shell`` action** runs one arbitrary command across the hosts *now*,
  outside the deploy model, and hands the outcome back as data -- a ``Result``
  TypedDict (``exit_status``/``stdout``/``stderr``), never an exception, even for
  a non-zero exit. ``gather_action`` fans it out concurrently and closes the hosts
  itself, so there is no manual ``aclose`` here (unlike ``gather_fact``).
* **The ``nginx_base`` deploy** is the reusable base the later steps compose:
  refresh apt, install nginx, and disable the stock "Welcome to nginx!" site by
  removing its ``sites-enabled/default`` symlink.

The target is the same persistent ``beta`` container as ``website_0.py`` -- start
it once with::

    podman run -d --name beta ubuntu:24.04 tail -f /dev/null

Because ``beta`` keeps its state between runs, driving ``nginx_base`` twice shows
idempotence: the first apply installs nginx and removes the symlink
(``[changed]``), the second finds the packages present and the symlink gone
(``[no change]``). ``apt.update`` is the deliberate exception -- it is stateless
and always runs, because a cache timestamp would drift between the prepare and
apply phases and trip the determinism guard.

Apply runs in **lockstep**, one request index at a time, so "disable the default
site" safely reads the symlink that "install nginx" just created -- no explicit
ordering hint or dependency. Branching on a *live fact* inside the deploy to
achieve that would raise ``DeterminismError``; ordering the two requests is what
works.

Commands run as the container's root user, so nothing needs ``sudo`` (a stock
``ubuntu:24.04`` container has no ``sudo`` binary); a real SSH target would pass
``sudo=True`` on each request. The nginx reload a config change usually needs is
the two-deploy-split handler pattern, which the production capstone
(``website_4.py``) shows -- ``systemctl`` needs a real init that a stock container
does not run as PID 1.

Run it with::

    uv run python examples/tutorials/website/website_1.py
"""

import asyncio
import sys

from reemote import (
    CommandError,
    Host,
    HostConnectionError,
    InventoryError,
    PrivilegeError,
    Shell,
    apt,
    files,
    format_log,
    format_plan,
    gather_action,
    op,
    run,
)

# The persistent local target from website_0.py: a container named ``beta``
# reached through the docker connector, so its state survives between runs.
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


def nginx_base(host: Host):
    """The shared base every web host gets: nginx installed, default site off.

    Three ordered requests. ``apt.update`` is stateless and always reports a
    change; ``apt.packages`` installs nginx only when it is missing;
    ``files.link`` with ``present=False`` removes the stock site's symlink only
    when it is there. ``packages`` is a tuple, not a list -- every request
    argument must hash, because the runner deduplicates on it.
    """
    yield op(apt.update, name="apt update")
    yield op(apt.packages, name="install nginx", packages=("nginx",))
    yield op(
        files.link,
        name="disable default site",
        path="/etc/nginx/sites-enabled/default",
        present=False,
    )


async def main() -> None:
    try:
        # -- The Shell action: an ad-hoc command across the fleet, as data ----
        # gather_action fans Shell.process out concurrently and returns a
        # {host.name: Result} mapping. It closes the hosts when it finishes, so
        # there is no manual aclose here (gather_fact leaves them open instead).
        hosts = [Host.from_inventory(entry) for entry in INVENTORY]
        results = await gather_action(
            hosts, Shell, command='. /etc/os-release && echo "$PRETTY_NAME"'
        )
        print("== Shell action (read-only, immediate) ==")
        for name, result in results.items():
            print(
                f"Shell {name}: exit={result['exit_status']} "
                f"stdout={result['stdout'].strip()!r}"
            )

        # -- nginx_base, twice: install, then converge ------------------------
        # One deploy drives both phases of each run; driving it a second time
        # against the persistent container shows the idempotent no-change.
        for attempt in (1, 2):
            plans, report, _ = await run(INVENTORY, nginx_base)
            print(f"\n== run {attempt}: plan (prepare) ==")
            print(format_plan(plans))
            if report is not None:
                print(f"== run {attempt}: log (apply) ==")
                print(format_log(report))
    except (CommandError, HostConnectionError, InventoryError, PrivilegeError) as exc:
        # An unreachable host (is beta running?), a failed command, a malformed
        # inventory, or a rejected elevation -- all typed, all naming the cause.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
