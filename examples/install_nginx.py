"""Example: install nginx and remove its default site on two hosts.

This is a *usage* example, not part of the library. It shows how to drive
reemote5's two-phase execution model end to end:

1. Build the deploy script as a generator yielding ``OperationRequest``\\ s.
2. Construct the target hosts (here, in-memory ``SimulatedHost`` stand-ins).
3. Run the prepare phase to get a plan, and the apply phase to execute it.

The trace mirrors the design doc section 9 scenario:

- ``web1`` starts fresh: installing nginx creates the default-site link, and
  the *next* operation's fresh fact cache sees that link and removes it.
- ``web2`` already has nginx installed (install reports "no change"), while
  the pre-existing default-site link is still removed.

Run it with::

    uv run python examples/install_nginx.py
"""

from collections.abc import Generator

from reemote5 import (
    OperationRequest,
    SimulatedHost,
    apt,
    files,
    format_log,
    format_plan,
    op,
    run,
)

NGINX_DEFAULT_SITE = "/etc/nginx/sites-enabled/default"
DEFAULT_TARGET = "/etc/nginx/sites-available/default"


def deploy(host: SimulatedHost) -> Generator[OperationRequest]:
    """Install nginx, then remove its default site.

    Branch-free, so the request sequence is identical in the prepare and
    apply phases (a branch on a mutable fact would raise DeterminismError).
    """
    yield op(apt.packages, name="Install nginx", packages=("nginx",))
    yield op(
        files.link,
        name="Remove default site",
        src=NGINX_DEFAULT_SITE,
        present=False,
    )


def make_hosts() -> list[SimulatedHost]:
    """web1 fresh; web2 already has nginx and its default-site link."""
    web1 = SimulatedHost(
        name="web1",
        installs_links={"nginx": {NGINX_DEFAULT_SITE: DEFAULT_TARGET}},
    )
    web2 = SimulatedHost(
        name="web2",
        packages={"nginx"},
        links={NGINX_DEFAULT_SITE: DEFAULT_TARGET},
    )
    return [web1, web2]


def main() -> None:
    # --dry: prepare phase only, nothing is sent to the hosts.
    dry_hosts = make_hosts()
    plans, _ = run(dry_hosts, deploy, dry=True)
    print("== Plan (prepare phase, --dry) ==")
    print(format_plan(plans))

    # Real run: prepare, then apply in lockstep across hosts.
    hosts = make_hosts()
    plans, report = run(hosts, deploy)
    print("\n== Run log (apply phase) ==")
    assert report is not None
    print(format_log(report))

    print("\n== Final host state ==")
    for host in hosts:
        print(f"{host.name}: packages={sorted(host.packages)} links={host.links}")


if __name__ == "__main__":
    main()
