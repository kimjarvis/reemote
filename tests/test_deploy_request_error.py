# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the deploy-body contract (``core.DeployRequestError``).

A deploy is a sync generator whose body *yields* the requests ``op()`` builds.
Three misuses of that shape used to fail with a bare, context-free error -- or
with no error at all -- so both points where the driver invokes a deploy body
(``drivers._prepare_host`` and the lockstep pull in ``drivers.run_apply``) now
check it:

- a body that never yields returns ``None`` instead of a request stream;
- a body that yields something other than an ``OperationRequest``;
- a body that builds a request with ``op()`` and drops it, which is the case
  nothing else can see, because the identically shorter request sequence
  satisfies the determinism guard while the operation silently never runs.

The third is caught by the per-invocation built-versus-yielded tally, and the
tests below pin both the positive detections and the shapes that must *not* be
flagged: ``compose(dedupe=True)`` collapses, and requests built outside the
tracked deploy invocation.
"""

import pytest

from reemote.core import (
    DeployRequestError,
    FactCache,
    HostRuntime,
    compose,
    op,
)
from reemote.drivers import run, run_apply, run_prepare
from reemote.modules import dnf
from tests.simulated_host import SimulatedHost

INVENTORY = [
    {"host": "web1", "connection": {"key_path": "~/.ssh/id_ed25519"}},
    {"host": "web2", "connection": {"key_path": "~/.ssh/id_ed25519"}},
]


def install_curl(host):
    """A well-formed single-request deploy, used as the positive control."""
    yield op(dnf.packages, name="Install curl", packages=("curl",))


# --- a body that never yields: the request stream is missing entirely -----------


async def test_deploy_that_never_yields_fails_in_prepare():
    """``op(...)`` without a ``yield``: the body returns ``None``, no stream.

    Checked on a ``dry=True`` run, which is prepare only, so the failure is
    pinned to the phase that must report it -- before anything is sent.
    """

    def deploy(host):
        op(dnf.packages, name="Install nginx", packages=("nginx",))

    with pytest.raises(DeployRequestError) as excinfo:
        await run(
            INVENTORY[:1], deploy, host_factory=SimulatedHost.from_inventory, dry=True
        )

    message = str(excinfo.value)
    assert "host web1" in message
    assert "returned None" in message
    assert "yield op(" in message


def test_deploy_request_error_is_a_type_error():
    """The misuse stays catchable as the bare ``TypeError`` it replaced."""
    assert issubclass(DeployRequestError, TypeError)


# --- a body that drops one of its requests: the silent case --------------------


async def test_request_built_but_not_yielded_fails_in_prepare():
    """One request yielded, another built and lost: caught by the tally."""

    def deploy(host):
        yield op(dnf.packages, name="Install curl", packages=("curl",))
        # Forgot the ``yield`` -- the sequence is valid, just silently shorter.
        op(dnf.packages, name="Install nginx", packages=("nginx",))

    with pytest.raises(DeployRequestError) as excinfo:
        await run(
            INVENTORY[:1], deploy, host_factory=SimulatedHost.from_inventory, dry=True
        )

    message = str(excinfo.value)
    assert "host web1 lost 1 built request" in message
    assert "prepare-phase" in message
    assert "silently dropped" in message


async def test_dropped_request_aborts_before_anything_is_sent():
    """A full (non-dry) run fails in prepare, so the host is never touched."""
    host = SimulatedHost.from_inventory(INVENTORY[0])

    def deploy(host):
        yield op(dnf.packages, name="Install curl", packages=("curl",))
        op(dnf.packages, name="Install nginx", packages=("nginx",))

    with pytest.raises(DeployRequestError):
        await run_prepare([host], deploy)

    assert host.packages == {}


# --- a yielded item that is not a request -------------------------------------


async def test_yielding_a_non_request_object_is_named_in_the_error():
    def deploy(host):
        yield "dnf.packages"

    with pytest.raises(DeployRequestError, match="not an OperationRequest") as excinfo:
        await run(
            INVENTORY[:1], deploy, host_factory=SimulatedHost.from_inventory, dry=True
        )

    assert "host web1" in str(excinfo.value)
    assert "str" in str(excinfo.value)


async def test_calling_an_operation_directly_fails_on_its_context():
    """The other half of the rule, characterised: an operation called from the
    deploy body cannot even be invoked, because ``OpContext`` is required."""

    def deploy(host):
        dnf.packages(packages=("nginx",))
        yield from ()

    with pytest.raises(TypeError) as excinfo:
        await run(
            INVENTORY[:1], deploy, host_factory=SimulatedHost.from_inventory, dry=True
        )

    assert "ctx" in str(excinfo.value)
    assert not isinstance(excinfo.value, DeployRequestError)


# --- the apply phase runs the same checks -------------------------------------


async def test_apply_phase_reports_a_dropped_request():
    """A hand-assembled runtime (no prepare phase behind it) is checked in apply."""
    host = SimulatedHost(name="web1")

    def deploy(host):
        yield op(dnf.packages, name="Install curl", packages=("curl",))
        op(dnf.packages, name="Install nginx", packages=("nginx",))

    runtime = HostRuntime(host, deploy(host), FactCache(host, phase="apply"))

    with pytest.raises(DeployRequestError, match="apply-phase"):
        await run_apply([runtime])

    # The yielded request ran; the dropped one aborted the run at the next pull.
    assert host.packages == {"curl": [""]}


async def test_apply_phase_aggregates_every_host_of_the_step():
    """A lockstep step pulls from all active hosts at once, so all report."""
    hosts = [SimulatedHost(name="web1"), SimulatedHost(name="web2")]

    def deploy(host):
        yield from ()
        op(dnf.packages, name="Install nginx", packages=("nginx",))

    runtimes = [
        HostRuntime(host, deploy(host), FactCache(host, phase="apply"))
        for host in hosts
    ]

    with pytest.raises(DeployRequestError) as excinfo:
        await run_apply(runtimes)

    message = str(excinfo.value)
    assert "host web1 lost 1 built request" in message
    assert "host web2 lost 1 built request" in message


# --- shapes that must not be flagged ------------------------------------------


async def test_compose_dedupe_collapses_are_not_lost_requests():
    """Two roles declaring the same prerequisite: dedupe, not a dropped request."""

    def role_baseline(host):
        yield op(dnf.packages, name="Install baseline tools", packages=("curl",))

    def role_httpd(host):
        yield op(dnf.packages, name="Install baseline tools", packages=("curl",))
        yield op(dnf.packages, name="Install httpd", packages=("httpd",))

    deploy = compose(role_baseline, role_httpd, dedupe=True)
    plans, report, hosts = await run(
        INVENTORY[:1], deploy, host_factory=SimulatedHost.from_inventory
    )

    assert [planned.request.name for planned in plans["web1"]] == [
        "Install baseline tools",
        "Install httpd",
    ]
    assert report is not None
    assert hosts[0].packages == {"curl": [""], "httpd": [""]}


async def test_requests_built_outside_the_deploy_invocation_are_untracked():
    """Pre-built requests: nothing was built under the counter, yet all are
    yielded, so the tally is negative and stays silent."""
    requests = [
        op(dnf.packages, name="Install curl", packages=("curl",)),
        op(dnf.packages, name="Install vim", packages=("vim",)),
    ]

    def deploy(host):
        yield from requests

    plans, _report, _hosts = await run(
        INVENTORY[:1], deploy, host_factory=SimulatedHost.from_inventory, dry=True
    )
    assert [planned.request.name for planned in plans["web1"]] == [
        "Install curl",
        "Install vim",
    ]


async def test_wellformed_deploy_with_several_yields_runs_clean():
    plans, report, hosts = await run(
        INVENTORY, install_curl, host_factory=SimulatedHost.from_inventory
    )

    assert len(plans["web1"]) == 1
    assert report is not None
    assert len(report.log) == 2
    assert hosts[0].packages == {"curl": [""]}
