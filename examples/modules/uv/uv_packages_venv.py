# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Manage one named virtualenv end to end with ``uv.packages(venv=...)``.

This is a *usage* example, not part of the library. It is the runnable
counterpart to the ``venv=`` prose in ``docs/modules/uv.md``: the sibling
``uv_packages_op.py`` deliberately sticks to the *unscoped* form, because a
scoped read of a virtual environment that does not exist yet fails on the
target -- ``uv pip list --format json --python <missing>`` exits 2 -- and
``run``'s prepare phase gathers that read before it has sent anything. Here the
deploy creates the venv itself and then manages packages inside it, so the
``--python`` flag is really issued, really scoped and really compared against
the environment it mutates.

Two things make that possible.

1. The **docker connector**, so no remote host is needed at all::

       {"host": "app1",
        "family": "Ubuntu",
        "connection": {"connector": "docker", "identifier": "ubuntu:24.04",
                       "docker_cmd": "podman"}}

   ``identifier`` is an image, so reemote starts a throwaway container from it
   (``podman run -d ubuntu:24.04 tail -f /dev/null``) and drives every fact read
   and command through ``docker exec``. The prerequisite is a local container
   CLI -- this example hardcodes rootless ``podman``, which needs no daemon;
   switch ``docker_cmd`` to ``"docker"`` for a Docker setup.
2. The **wave driver**, ``run_waves``. Unlike ``run``, it materializes and
   applies one request per step before pulling the next, so there is no
   prepare/apply window for the container's state to be stale in: the scoped
   ``Packages`` read happens *after* ``uv venv`` has run. That per-step
   ordering is exactly what a bootstrap chain needs -- each request below
   depends on the state the one before it left behind, which is why they cannot
   all ride one ``run()``.

The deploy therefore reads as a build-up and a demonstration -- one request per
step, every step inside ``wave 0``, since a wave only ends at a barrier::

    step 1  apt.packages   python3 + python3-pip (apt update first)
    step 2  pip.packages   uv, with --break-system-packages
    step 3  uv.venv        create /srv/app/.venv
    step 4  uv.packages    six==1.16.0 into the venv, venv=VENV
    step 5  uv.packages    the same request again  -> [no change]
    step 6  uv.packages    present=False, venv=VENV
    step 7  uv.packages    the same removal again  -> [no change]

Stock ``ubuntu:24.04`` carries no ``python3``, and noble's apt suite carries no
``uv``, so the first two steps install the tool the later ones need -- through
real operations rather than a raw shell. The venv itself comes from
``uv.venv``, and only then does ``venv=VENV`` name it to ``uv.packages``: the
option rides onto the ``Packages`` fact read *and* every command as uv's
``--python``, so the listing the operation compares against is the environment
it mutates. The log lines that follow are the point of the example -- ``uv pip
install --no-progress --python /srv/app/.venv six==1.16.0``, then the scoped
uninstall fed the *bare* name. Neither the install nor the uninstall needs
elevation: ``docker exec`` runs as the container's default user (root here),
and a stock ubuntu image has no ``sudo`` binary anyway, so no request carries
``sudo=True``. The same commands on an SSH target would flag the *system*
interpreter's site-packages only; a virtualenv is never root-owned.

There is deliberately no ``dry=True`` preview here, unlike the SSH examples. A
dry wave plans every request against pristine state and applies nothing, so the
scoped read would run before ``uv venv`` does and exit 2 -- the very constraint
this script is built around. The no-op repeats are still shown, because the
wave driver re-reads the scoped listing per step: within one run the second
install and the second removal genuinely report ``[no change: ...]``.

The container outlives none of it: ``run_waves`` owns the hosts it materializes
and closes them once, which commits the container into a new image (its short ID
is printed from ``DockerHost.image_id``) and removes it. Nothing lingers, and a
rerun starts pristine from ``ubuntu:24.04`` again -- so the convergence on
display is the one *inside* the run, not across runs. Point ``identifier`` at a
persistent named container instead (``podman run -d --name app1 ubuntu:24.04
tail -f /dev/null``) and the state survives, since reemote never commits or
removes a container it did not create.

Run it with::

    uv run python examples/modules/uv/uv_packages_venv.py
"""

import asyncio
import sys
from collections.abc import Generator

from reemote import (
    CommandError,
    DockerHost,
    Host,
    HostConnectionError,
    apt,
    format_waves,
    op,
    pip,
    run_waves,
    uv,
)
from reemote.stages import StageItem

INVENTORY = [
    {
        "host": "app1",
        "family": "Ubuntu",
        "connection": {
            "connector": "docker",
            "identifier": "ubuntu:24.04",
            "docker_cmd": "podman",
        },
    },
]

# The virtual environment every scoped request names. A directory the deploy
# itself creates one step earlier, so the scoped ``Packages`` read finds it.
VENV = "/srv/app/.venv"

# Module-level tuples throughout: ``op()`` builds an ``OperationRequest`` whose
# arguments must stay hashable.
RUNTIME_PACKAGES = ("python3", "python3-pip")
UV_PACKAGE = ("uv",)
PACKAGES = ("six==1.16.0",)


def deploy(host: Host) -> Generator[StageItem]:
    """Bootstrap uv, create the venv, then manage a package inside it.

    One request per step, in dependency order: each is planned only after the
    previous one has been applied to the container, which is what lets the
    later reads (``pip3 freeze``, ``uv pip list --python``) ask about state the
    earlier ones installed. Nothing here needs elevation -- the container's
    default user is root -- and the body stays branch-free, so every host in the
    inventory pulls the same sequence.
    """
    # 1. The interpreter pip and uv need, refreshing apt's index first: a stock
    #    ubuntu image has no package lists and no python3 at all.
    yield op(
        apt.packages,
        name="Install python3 and pip",
        packages=RUNTIME_PACKAGES,
        update=True,
    )
    # 2. uv itself, through the system pip. noble ships no uv package, and
    #    PEP 668 marks the system interpreter externally managed, so the
    #    install needs pip's --break-system-packages to have anywhere to write.
    yield op(
        pip.packages,
        name="Install uv",
        packages=UV_PACKAGE,
        pip="pip3",
        extra_install_args="--break-system-packages",
    )
    # 3. The environment the rest of the run targets: ``uv venv``, idempotent on
    #    its own existence probe.
    yield op(uv.venv, name="Create the app venv", path=VENV)
    # 4. The scoped install. ``venv=VENV`` becomes uv's ``--python`` on the
    #    ``Packages`` read and on the install command.
    yield op(
        uv.packages,
        name="Install six into the venv",
        packages=PACKAGES,
        venv=VENV,
    )
    # 5. The same request again: the venv now carries the pin, so the wave
    #    reports no change and sends nothing.
    yield op(
        uv.packages,
        name="Re-run the scoped install",
        packages=PACKAGES,
        venv=VENV,
    )
    # 6. And the scoped removal, fed bare names (``uv pip uninstall`` rejects
    #    ``==`` tokens) with the same ``--python``.
    yield op(
        uv.packages,
        name="Remove six from the venv",
        packages=PACKAGES,
        present=False,
        venv=VENV,
    )
    # 7. Its no-op repeat, proving the removal is scoped too: an unscoped
    #    listing would not know the venv has gone empty again.
    yield op(
        uv.packages,
        name="Re-run the scoped removal",
        packages=PACKAGES,
        present=False,
        venv=VENV,
    )


async def main() -> None:
    try:
        # A single wave-deep run with no barriers: each request is planned and
        # applied in its own step, so the scoped reads below see the interpreter,
        # the tool and the venv that the earlier steps installed.
        report, hosts = await run_waves(INVENTORY, deploy)

        print("== Wave tree (each request planned after the last was applied) ==")
        print(format_waves(report))

        # ``run_waves`` closed the hosts, committing the throwaway container
        # into a new image and removing it; the short image ID is on the host.
        for host in hosts:
            assert isinstance(host, DockerHost)
            print(f"\n== Committed image for {host.name}: {host.image_id} ==")
    except HostConnectionError as exc:
        # The docker CLI is missing, the daemon is unreachable or the image
        # could not be started -- for an image that cannot be pulled, this is
        # also where the connector's own advice appears. One line naming every
        # failed target, no subprocess traceback.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except CommandError as exc:
        # A command inside the container exited non-zero: a failed apt/pip
        # bootstrap, or a scoped ``uv pip list`` whose venv is not there. The
        # typed message names the host, the command and stderr.
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    asyncio.run(main())
