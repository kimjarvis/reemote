# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: the wave tree — ``format_waves`` on a two-role program.

``run_waves`` returns a ``WaveReport``, and §7.2's tree is the design's *main
user-visible output*: the whole point of parking a body at a barrier is that
the run is no longer a flat list of commands but a scope-then-wave tree, with
each barrier printed inside the wave it closed. ``report.tree()`` and the
module-level ``format_waves(report)`` render the same string — the method is
just an alias — and nothing else in the corpus prints it, so this file is the
one that does.

Two library-shaped roles are composed into a single program to make the tree
non-trivial on its own:

- ``base_role`` installs the runtime and reads it back through a ``fact``
  barrier;
- ``site_role`` writes the config and starts the service.

``scoped("base", ...)`` and ``scoped("site", ...)`` label the two blocks, so
``format_waves`` groups first by *scope path*, then by *wave* (§7.2) — the
``(root)`` group holds the program's own stages, and a barrier-only wave shows
up with nothing but its ``barrier:`` line. Because every line still renders
through ``format_plan``/``format_log``, a wave-tree line is byte-identical to
what a plain ``run()`` example prints; only the headings are new.

Runs **entirely in memory**; the project root is added to ``sys.path`` to
import the test framework's ``SimulatedHost``.

Run it with::

    python examples/demonstrations/waves/run_waves_report.py
"""

import asyncio
import sys
from collections.abc import Generator
from pathlib import Path

from reemote import Host, dnf, files, format_waves, op, run_waves, systemd
from reemote.stages import Deploy, StageItem, fact, scoped

# examples/demonstrations/waves/run_waves_report.py -> the project root, so
# ``tests`` is importable (running a script by path puts its own folder, not
# the root, on sys.path).
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.simulated_host import SimulatedHost

INVENTORY = [
    {"host": "web1", "family": "RedHat", "connection": {"host": "web1.example"}},
    {"host": "web2", "family": "RedHat", "connection": {"host": "web2.example"}},
]

CONF = "/etc/webapp/app.conf"


def base_role() -> Deploy:
    """The runtime role: install the package, then *read* it back.

    The ``fact`` barrier closes this scope's wave, so the tree carries a
    ``barrier:`` line here even though no request follows it in the same wave.
    """

    def deploy(host: Host) -> Generator[StageItem]:
        yield op(
            dnf.packages,
            name="Install runtime",
            packages=("python3",),
            sudo=True,
        )
        yield fact(dnf.DnfPackages, name="Check runtime")

    return deploy


def site_role() -> Deploy:
    """The application role: write the config, then run the service.

    Both are plain requests, so they land together in one wave of the
    ``site`` scope group — no barrier, no ``barrier:`` line.
    """

    def deploy(host: Host) -> Generator[StageItem]:
        yield op(
            files.install,
            name="Write app config",
            dest=CONF,
            content="listen = 8080\n",
            mode="0644",
            sudo=True,
        )
        yield op(
            systemd.service,
            name="Run webapp",
            service="webapp",
            running=True,
            enabled=True,
            sudo=True,
        )

    return deploy


def program(host: Host) -> Generator[StageItem]:
    """Compose both roles under labels, plus the program's own stage.

    ``yield from scoped(label, deploy)(host)`` runs the child in the *same*
    stream and tags its report block; the ``Preflight`` op at the top belongs
    to no scope, so it lands in the ``(root)`` group — that is what makes the
    tree show more than one group.
    """
    yield op(files.directory, name="Preflight", path="/var/lib/webapp", sudo=True)
    yield from scoped("base", base_role())(host)
    yield from scoped("site", site_role())(host)


def factory(entry: dict) -> SimulatedHost:
    """``host_factory``: bare simulated hosts — nothing pre-converged."""
    return SimulatedHost.from_inventory(entry)


async def main() -> None:
    report, _hosts = await run_waves(INVENTORY, program, host_factory=factory)
    assert report is not None

    # The design's headline output, printed by the module-level renderer.
    tree = format_waves(report)
    print("== format_waves(report): grouped by scope, then wave ==")
    print(tree)

    # The method is a pure alias for the function — one string, two names —
    # so a caller who has the report can reach it either way.
    assert tree == report.tree(), "tree() must be format_waves()"

    # Structure the tree makes visible: three scope groups (the program's own
    # (root) stage, plus the two labelled roles) and one barrier line, from
    # base_role's fact, sitting in the wave it closed.
    groups = {line for line in tree.splitlines() if not line.startswith(" ")}
    assert {"(root)", "base", "site"} <= groups, groups
    assert "barrier:" in tree and "Check runtime" in tree
    print(
        "\nscope groups: (root) + base + site; "
        "the 'barrier: Check runtime' line is base_role's fact, "
        "printed inside the wave it parked — format_waves is the only renderer"
    )


if __name__ == "__main__":
    asyncio.run(main())
