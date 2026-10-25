"""Tests for the generator-based two-phase execution model.

Covers the properties from the design document: request descriptor
semantics (4.1), fact caching (4.4), sequential "act, then read"
semantics in apply (8.2/9), cross-host lockstep (8.2), --dry behavior
(8.1), determinism checking (11.1) and change reporting (10).

The apply path (``send_command`` and the drivers calling it) is async in
preparation for an asyncssh transport, and so is fact gathering: a fact's
``process`` is a coroutine, making ``FactCache.get`` and the operations that
await it async too. ``asyncio_mode = "auto"`` lets the async tests below run
without decorators.
"""

import copy
from collections.abc import Generator

import pytest

from reemote5.core import FactCache, OpContext, OperationRequest, op
from reemote5.drivers import (
    DeterminismError,
    run,
    run_apply,
    run_prepare,
    send_command,
)
from reemote5.facts import AptPackages, FileLink
from reemote5.hosts import Host
from reemote5.operations import apt, files
from reemote5.operations.apt import AptInstallCommand
from reemote5.operations.files import LinkCommand
from tests.simulated_host import SimulatedHost

NGINX_DEFAULT_SITE = "/etc/nginx/sites-enabled/default"
DEFAULT_TARGET = "/etc/nginx/sites-available/default"


# --- Test setup: example inventory, deploy script and host factory ----------
#
# The library provides the execution model (drivers, core, operations,
# facts, Host) and accepts the inventory as a parameter. SimulatedHost is
# part of the test framework: it lives in tests/simulated_host.py (never in
# src/reemote5/) and is injected via ``run``'s ``host_factory`` argument,
# never constructed by the library. The specific nginx deploy script and
# the web1/web2 inventory and initial state below are test fixtures, not
# part of the library.

WEB_INVENTORY = [
    {
        "host": "web1",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
        "session": {"term_type": "xterm-color"},
    },
    {
        "host": "web2",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
        "session": {"term_type": "xterm-color", "term_size": 80},
    },
]

# Simulated pre-deployment state per host name (test-framework data, NOT
# inventory): web1 fresh, web2 already configured (design doc section 9).
WEB_INITIAL_STATE = {
    "web1": {"installs_links": {"nginx": {NGINX_DEFAULT_SITE: DEFAULT_TARGET}}},
    "web2": {
        "packages": {"nginx"},
        "links": {NGINX_DEFAULT_SITE: DEFAULT_TARGET},
    },
}


def simulated_web_factory(entry: dict) -> SimulatedHost:
    """``host_factory`` building SimulatedHosts with the section-9 state."""
    host = SimulatedHost.from_inventory(entry)
    # Deep-copy: send_command mutates the host's dicts in place, which must
    # not leak back into the shared fixture.
    for attr, value in WEB_INITIAL_STATE.get(host.name, {}).items():
        setattr(host, attr, copy.deepcopy(value))
    return host


def deploy(host: Host) -> Generator[OperationRequest]:
    """Example deploy script (design doc section 7/9).

    Install nginx, then remove its default site. Branch-free, so it is
    deterministic across the prepare and apply phases.
    """
    yield op(apt.packages, name="Install nginx", packages=("nginx",))
    yield op(
        files.link,
        name="Remove default site",
        src=NGINX_DEFAULT_SITE,
        present=False,
    )


# --- Inventory / Host (library input) ----------------------------------------


def test_host_from_inventory_maps_known_and_extra_keys():
    host = Host.from_inventory(
        {
            "host": "web1",
            "connection": {"key_path": "~/.ssh/id_ed25519"},
            "session": {"term_type": "xterm-color"},
            "sudo": {"password": "secret"},
            "groups": ["web"],  # unrecognised key -> preserved in extra
        }
    )
    assert host.name == "web1"
    assert host.connection == {"key_path": "~/.ssh/id_ed25519"}
    assert host.session == {"term_type": "xterm-color"}
    assert host.sudo == {"password": "secret"}
    assert host.extra == {"groups": ["web"]}


async def test_inventory_host_reaches_the_real_transport_boundary():
    """A plain Host carries connection/session into facts and operations,
    where the missing real SSH transport is reported explicitly."""
    host = Host.from_inventory(WEB_INVENTORY[0])
    assert host.connection == {"key_path": "~/.ssh/id_ed25519"}
    with pytest.raises(NotImplementedError):
        await AptPackages.process(host)
    with pytest.raises(NotImplementedError):
        await send_command(host, AptInstallCommand(packages=("nginx",)))


# --- OperationRequest (4.1) -------------------------------------------------


def test_operation_request_kwargs_are_copied():
    kwargs = {"packages": ("nginx",)}
    request = OperationRequest(func=apt.packages, kwargs=kwargs, name="Install nginx")
    kwargs["packages"] = ("vim",)  # mutating the caller's dict...
    assert request.kwargs["packages"] == ("nginx",)  # ...must not leak in


def test_operation_request_value_equality_and_hashing():
    a = op(apt.packages, name="Install nginx", packages=("nginx",))
    b = op(apt.packages, name="Install nginx", packages=("nginx",))
    assert a == b
    assert hash(a) == hash(b)
    assert a != op(apt.packages, name="Install nginx", packages=("vim",))


# --- FactCache (4.4) ---------------------------------------------------------


async def test_fact_cache_caches_per_key():
    host = SimulatedHost(name="h", packages={"nginx"})
    cache = FactCache(host, phase="prepare")
    assert await cache.get(AptPackages) == {"nginx"}

    host.packages.add("vim")
    # Cached: the mutation is not visible through the same cache...
    assert await cache.get(AptPackages) == {"nginx"}
    # ...but a fresh cache sees it, which is exactly the per-step
    # recreation the apply driver relies on.
    assert await FactCache(host, phase="apply").get(AptPackages) == {"nginx", "vim"}


async def test_fact_cache_keys_include_args():
    host = SimulatedHost(name="h", links={"/a": "/target-a"})
    cache = FactCache(host, phase="prepare")
    assert await cache.get(FileLink, "/a") == "/target-a"
    assert await cache.get(FileLink, "/b") is None


# --- Operations (6) ----------------------------------------------------------


async def test_apt_packages_sets_changed_and_yields_command():
    host = SimulatedHost(name="h")
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.packages(ctx, packages=("nginx", "vim"))]
    assert ctx.changed
    assert ctx.reason == "2 package(s) to install"
    assert commands == [AptInstallCommand(packages=("nginx", "vim"))]


async def test_apt_packages_no_change_reason_recorded():
    host = SimulatedHost(name="h", packages={"nginx"})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in apt.packages(ctx, packages=("nginx",))]
    assert not ctx.changed
    assert ctx.reason == "all packages present"  # recorded even when unchanged (10)
    assert commands == []


async def test_files_link_remove_when_present():
    host = SimulatedHost(name="h", links={NGINX_DEFAULT_SITE: DEFAULT_TARGET})
    ctx = OpContext(host=host, facts=FactCache(host, phase="prepare"))
    commands = [c async for c in files.link(ctx, src=NGINX_DEFAULT_SITE, present=False)]
    assert ctx.changed
    assert ctx.reason == "link present, will remove"
    assert commands == [LinkCommand(src=NGINX_DEFAULT_SITE, target=None)]


# --- Apply phase: sequential facts and lockstep (8.2, 9) ---------------------


async def test_apply_sequential_facts_the_section_9_trace():
    _plans, report, (web1, web2) = await run(
        WEB_INVENTORY, deploy, host_factory=simulated_web_factory
    )

    by_host: dict[str, list] = {}
    for entry in report.log:
        by_host.setdefault(entry.host, []).append(entry)

    # web1: nginx installed, which creates the default-site link; the next
    # operation's fresh fact cache sees it and removes it.
    w1_install, w1_link = by_host["web1"]
    assert w1_install.changed and w1_install.commands == (
        AptInstallCommand(packages=("nginx",)),
    )
    assert w1_link.changed and w1_link.reason == "link present, will remove"
    assert w1_link.commands == (LinkCommand(src=NGINX_DEFAULT_SITE, target=None),)

    # web2: nginx already present (no-op), but the default-site link
    # still exists pre-deployment, so its removal is a real change.
    w2_install, w2_link = by_host["web2"]
    assert not w2_install.changed and w2_install.reason == "all packages present"
    assert w2_link.changed and w2_link.reason == "link present, will remove"

    # Final state: nginx installed on both, default site gone on both.
    for host in (web1, web2):
        assert host.packages == {"nginx"}
        assert host.links == {}


async def test_apply_is_lockstep_across_hosts():
    """All hosts execute their Nth request before any pulls request N+1."""
    hosts = [
        SimulatedHost(name=name, installs_links={"nginx": {"/l": "/t"}})
        for name in ("a", "b", "c")
    ]
    runtimes, plans = await run_prepare(hosts, deploy)
    report = await run_apply(runtimes, plans=plans)
    # The driver executes step by step over all hosts, so the log must be
    # grouped by operation index: three installs, then three link removals.
    assert [entry.name for entry in report.log] == [
        "Install nginx",
        "Install nginx",
        "Install nginx",
        "Remove default site",
        "Remove default site",
        "Remove default site",
    ]


async def test_hosts_finishing_at_different_steps():
    """A host whose generator ends early does not stall the others."""

    def uneven_deploy(host):
        yield op(apt.packages, name="Install nginx", packages=("nginx",))
        if host.name == "b":
            yield op(apt.packages, name="Install vim", packages=("vim",))

    _plans, report, (a, b) = await run(
        [{"host": "a"}, {"host": "b"}],
        uneven_deploy,
        host_factory=SimulatedHost.from_inventory,
    )
    assert [entry.name for entry in report.log] == [
        "Install nginx",
        "Install nginx",
        "Install vim",
    ]
    assert a.packages == {"nginx"}
    assert b.packages == {"nginx", "vim"}


# --- Prepare phase / --dry (8.1, 10) ------------------------------------------


async def test_dry_run_reports_plan_without_touching_hosts():
    plans, report, (web1, _web2) = await run(
        WEB_INVENTORY, deploy, host_factory=simulated_web_factory, dry=True
    )

    assert report is None
    # The plan reflects pre-deployment state: on web1 the link does not
    # exist yet, so the removal is reported as a no-op (section 9 prepare).
    assert [p.changed for p in plans["web1"]] == [True, False]
    assert plans["web1"][0].reason == "1 package(s) to install"
    assert plans["web1"][1].reason == "link already absent"
    # web2: nginx present (no change), link present (would be removed).
    assert [p.changed for p in plans["web2"]] == [False, True]
    assert plans["web2"][1].reason == "link present, will remove"
    # Hosts untouched.
    assert web1.packages == set()
    assert web1.links == {}


async def test_prepare_materializes_commands_for_changed_ops():
    plans, _, _hosts = await run(
        WEB_INVENTORY[:1], deploy, host_factory=simulated_web_factory, dry=True
    )
    install = plans["web1"][0]
    assert install.commands == (AptInstallCommand(packages=("nginx",)),)


# --- Determinism check (11.1) --------------------------------------------------


async def test_determinism_violation_is_a_hard_error():
    """Branching on a mutable fact diverges between phases -> hard error."""

    def nondeterministic_deploy(host):
        yield op(apt.packages, name="Install nginx", packages=("nginx",))
        if "nginx" in host.packages:  # mutable-fact branch: forbidden
            yield op(apt.packages, name="Install vim", packages=("vim",))
        else:
            yield op(apt.packages, name="Install emacs", packages=("emacs",))

    host = SimulatedHost(name="h")
    runtimes, plans = await run_prepare([host], nondeterministic_deploy)
    with pytest.raises(DeterminismError, match="diverges"):
        await run_apply(runtimes, plans=plans)
