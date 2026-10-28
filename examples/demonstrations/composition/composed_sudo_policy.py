"""Example: a site-wide privilege policy over unprivileged third-party roles.

This is a *usage* example, not part of the library. It builds on
``dnf_packages.py`` (same inventory, same two-phase ``run`` flow over the
real asyncssh transport) and shows the third composition scenario: role
functions written elsewhere -- here, as if by a colleague targeting a root
login -- that carry no ``sudo`` flags at all, adopted under a site policy
that connects as an ordinary user and elevates everywhere.

``sub_deploy(role_redis, sudo=True)`` retro-fits the policy without editing
the role: ``sudo=True`` is a *forced-elevation* policy that fills in every
request which has no opinion of its own. ``sudo``/``su`` are a tri-state
(``reemote5.core.Privilege``: ``ON``/``OFF``/``UNSET``), so the inheritance
rule is *inner wins* -- a request that already set ``sudo=True`` (``ON``)
passes through untouched, an unflagged one (``UNSET``) is elevated by the
policy, and a request that deliberately set ``sudo=False`` (``OFF``) opts out
and stays unprivileged. That opt-out is exactly what a plain bool could not
express: a bool cannot tell "run this one unprivileged" from "never flagged",
so a ``sudo=True`` wrapper elevated both. The wrapping is a pure request-stream
transform (``dataclasses.replace`` over the frozen ``OperationRequest``), so
prepare and apply flatten identically and the determinism guard holds.

The role below shows all three states: the install omits the flag (``UNSET``,
elevated by the policy), the boot link already asks for ``sudo=True`` (``ON``,
preserved), and a healthcheck marker deliberately sets ``sudo=False`` (``OFF``)
to stay unprivileged even under the site-wide policy -- it only touches a
world-writable path, so it needs no root. In the run log the elevated commands
render with their ``sudo`` prefix (``$ sudo dnf install -y redis``) and the
opt-out does not (``$ touch /tmp/redis-managed``), since ``format_log`` maps
descriptors through the same ``command_shell`` the real transport uses.

Run it with::

    uv run python examples/demonstrations/composition/composed_sudo_policy.py
"""

import asyncio
from collections.abc import Generator

from reemote5 import (
    Host,
    OperationRequest,
    compose,
    dnf,
    files,
    format_log,
    format_plan,
    op,
    run,
    sub_deploy,
)

REDIS_UNIT = "/usr/lib/systemd/system/redis.service"
REDIS_WANTS_LINK = "/etc/systemd/system/multi-user.target.wants/redis.service"

# Same real-host inventory as dnf_packages.py; the default host_factory
# materializes each entry into a plain Host connecting over asyncssh.
INVENTORY = [
    {
        "host": "web1",
        "connection": {
            "address": "192.168.0.240",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
    {
        "host": "web2",
        "connection": {
            "address": "192.168.0.242",
            "username": "kjarvis",
            "key_path": "/home/kim/.ssh/id_ed25519",
        },
    },
]


def role_redis(host: Host) -> Generator[OperationRequest]:
    """A colleague's role, written for a root login: mostly no sudo flags.

    Do not "fix" it by sprinkling ``sudo=True`` through the body -- the
    point of the example is that the elevation policy lives at the
    composition site, not inside the role. The one ``sudo=False`` below is
    not a fix either: it is a deliberate opt-out for the single command that
    must stay unprivileged, which the tri-state lets the policy honour.
    """
    yield op(dnf.packages, name="Install redis", packages=("redis",))
    yield op(
        files.link,
        name="Enable redis at boot",
        src=REDIS_WANTS_LINK,
        target=REDIS_UNIT,
        sudo=True,  # the author did flag this one; the policy preserves it
    )
    yield op(
        files.file,
        name="Write healthcheck marker",
        src="/tmp/redis-managed",
        present=True,
        sudo=False,  # deliberate opt-out: stays unprivileged under the policy
    )


# The policy statement: everything this role yields runs elevated.
deploy = compose(sub_deploy(role_redis, name_prefix="redis", sudo=True))


async def main() -> None:
    # --dry: prepare phase only. The plan already carries the wrapper's
    # names and privilege flags; no mutating command is sent.
    plans, _, _hosts = await run(INVENTORY, deploy, dry=True)
    print("== Plan (prepare phase, --dry) ==")
    print(format_plan(plans))

    # Real run: prepare, then apply in lockstep across hosts over SSH.
    plans, report, _hosts = await run(INVENTORY, deploy)
    print("\n== Run log (apply phase) ==")
    assert report is not None
    print(format_log(report))


if __name__ == "__main__":
    asyncio.run(main())
