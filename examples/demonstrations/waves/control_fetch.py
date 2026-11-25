# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Example: a ``control`` barrier whose input comes from a per-host fact.

The §4.4 / §16.4 split of control-side IO: work identical for every host
belongs in an ``async def`` role factory, awaited once before the first pull;
work whose *input* is per-host state — a URL interpolated with the
distribution a host reported — cannot run before the deploy does, so it is a
``control`` barrier: the driver awaits the given coroutine function on the
controller and resumes the body with its return value. ``repo_epel`` fetches
its GPG key exactly this way (§18.1).

Two traps this makes visible. First, the *mis*split: hoisting this fetch into
a factory would pin one URL for every host — the RedHat host and the Ubuntu
host would share a key that fits neither. Second, a ``control`` barrier
returns **str, not a stream** (§16.10): the body wraps the text in its own
``io.BytesIO`` — built inside, once, because the wave loop has a single pass
and a module-level stream would be handed out consumed to every host after
the first.

Runs **entirely in memory** like the other wave demonstrations. The fetch is
a local stub with ``files.fetch_url``'s shape (it returns the armored text) so
the sweep never touches the network; swapping in the real function changes
nothing about the stream. ``SimulatedHost`` is test-framework scaffolding, so
the project root is added to ``sys.path`` to import it.

Run it with::

    python examples/demonstrations/waves/control_fetch.py
"""

import asyncio
import io
import sys
from collections.abc import Generator
from pathlib import Path

from reemote import Host, files, op, run_waves
from reemote.modules.server import LinuxDistribution
from reemote.stages import StageItem, control, fact

# examples/demonstrations/waves/control_fetch.py -> the project root, so
# ``tests`` is importable (running a script by path puts its own folder, not
# the root, on sys.path).
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.simulated_host import SimulatedHost

# One RedHat and one Ubuntu host: the whole point is that the *same* body
# fetches a *different* URL per host because the fact differs per host.
INVENTORY = [
    {"host": "rh1", "family": "RedHat", "connection": {"host": "rh1.example"}},
    {"host": "ub1", "family": "Ubuntu", "connection": {"host": "ub1.example"}},
]

KEYRING = "/etc/pki/rpm-gpg/RPM-GPG-KEY-demo"
APT_KEY = "/usr/share/keyrings/demo-archive-keyring.gpg"


async def fetch_key(url: str) -> str:
    """Control-side fetch, shaped exactly like ``files.fetch_url``.

    Returns the fetched *text*, never a stream — the same contract §16.10
    pins, so the body's wrapping is identical whether the bytes came from a
    stub or the network. The URL differs per host, so the returned text does
    too, which the assert in main() checks.
    """
    return f"-----BEGIN PGP PUBLIC KEY BLOCK-----\n# fetched from {url}\n-----END-----\n"


def factory(entry: dict) -> SimulatedHost:
    """``host_factory`` seeding each host's simulated distribution."""
    host = SimulatedHost.from_inventory(entry)
    if host.name == "rh1":
        host.linux_distribution = {"name": "CentOS", "family": "RedHat", "major": "9"}
    else:
        host.linux_distribution = {"name": "Ubuntu", "family": "Debian", "major": "24.04"}
    return host


def deploy(host: Host) -> Generator[StageItem]:
    """Read the distribution, fetch *its* key control-side, install it here.

    The stream: one request stage, a ``fact`` barrier feeding a ``control``
    barrier feeding a request stage — three different execution loci (host
    command, host read, controller await) in one generator body.
    """
    yield op(
        files.directory,
        name="Ensure keyring dir",
        path="/tmp/demo-keys",
    )

    # Wave-closing read: what is this machine, really? The declared family is
    # a hint; the fact is the answer the URL is built from.
    distro = yield fact(LinuxDistribution, name="Which distribution")
    if distro["family"] == "RedHat":
        url = "https://example.invalid/keys/redhat.asc"
        dest = KEYRING
    else:
        url = "https://example.invalid/keys/ubuntu.asc"
        dest = APT_KEY

    # The control barrier: an await *on the controller* whose input is this
    # host's fact result — the shape that cannot move into an async factory
    # without pinning one URL for every host. It returns str, not a stream.
    key_text = yield control(fetch_key, url, name="Fetch archive key")

    # Built here, in the single pass, per host: a module-level stream would be
    # rewound-to-empty for every host after the first (the §6.2 inversion).
    stream = io.BytesIO(key_text.encode("utf-8"))
    yield op(
        files.put,
        name="Stage key file",
        src=stream,
        dest=f"/tmp/demo-keys/{host.name}.asc",
    )
    yield op(
        files.install,
        name="Install key file",
        dest=dest,
        content=key_text,
        mode="0644",
        sudo=True,
    )


async def main() -> None:
    # The two hosts' control barriers receive *different* arguments — proof
    # the split is load-bearing, not decorative.
    report, hosts = await run_waves(INVENTORY, deploy, host_factory=factory)
    assert report is not None
    print("== Wave tree: fact feeds a per-host control fetch feeds a request ==")
    print(report.tree())

    staged = [
        entry
        for entry in report.merged().log
        if entry.name == "Stage key file" and entry.changed
    ]
    assert len(staged) == 2, staged
    by_name = {h.name: h for h in hosts}
    rh_bytes = by_name["rh1"].remote_files["/tmp/demo-keys/rh1.asc"]
    ub_bytes = by_name["ub1"].remote_files["/tmp/demo-keys/ub1.asc"]
    assert b"redhat" in rh_bytes and b"ubuntu" in ub_bytes, (rh_bytes, ub_bytes)
    print("\nper-host URLs fetched control-side: redhat.asc and ubuntu.asc (not one shared key)")


if __name__ == "__main__":
    asyncio.run(main())
