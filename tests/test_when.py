# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the ``when`` eligibility guard (meta-parameter, composition, driver).

Covers the guard's value semantics (normalization to a predicate tuple,
participation in equality/hash and effective identity), conjunction at the
``sub_deploy`` composition site (outer guards first, restrict-only), and the
driver's skip behaviour end to end through ``run`` with ``SimulatedHost``:
a guarded request keeps its lockstep slot on every host, is never
materialized on an ineligible host, and renders as ``[skipped: ...]`` in
both the plan and the run log.
"""

from collections.abc import Generator

import pytest

from reemote5.core import OperationRequest, compose, op, sub_deploy
from reemote5.drivers import DeterminismError, format_log, format_plan, run
from reemote5.hosts import Host
from reemote5.modules import dnf
from tests.simulated_host import SimulatedHost

INVENTORY = [
    {
        "host": "web1",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
        "tags": ["web", "canary"],
    },
    {
        "host": "web2",
        "connection": {"key_path": "~/.ssh/id_ed25519"},
        "tags": ["web"],
    },
]

HOST = Host.from_inventory(INVENTORY[0])


# Stable, module-level predicates: the same function objects in both phases,
# exactly like the operation functions the requests reference.
def is_canary(host: Host) -> bool:
    return "canary" in host.tags


def is_web(host: Host) -> bool:
    return "web" in host.tags


def never(host: Host) -> bool:
    return False


def simulated_factory(entry: dict) -> SimulatedHost:
    return SimulatedHost.from_inventory(entry)


def deploy_with_canary(host: Host) -> Generator[OperationRequest]:
    """One shared schedule: baseline everywhere, canary tooling guarded."""
    yield op(dnf.packages, name="Install baseline", packages=("curl",), sudo=True)
    yield op(
        dnf.packages,
        name="Install canary tooling",
        packages=("strace",),
        sudo=True,
        when=is_canary,
    )


# --- Value semantics: normalization, equality, effective identity --------------


def test_op_when_defaults_to_empty_tuple_and_is_not_forwarded():
    request = op(dnf.packages, packages=("curl",))
    assert request.when == ()
    assert request.kwargs == {"packages": ("curl",)}  # when never reaches func


def test_when_normalizes_callable_tuple_and_none_to_a_tuple():
    assert op(dnf.packages, when=is_canary).when == (is_canary,)
    assert op(dnf.packages, when=(is_canary, is_web)).when == (is_canary, is_web)
    assert op(dnf.packages, when=None).when == ()


def test_when_participates_in_equality_and_hash():
    unguarded = op(dnf.packages, packages=("curl",))
    guarded = op(dnf.packages, packages=("curl",), when=is_canary)
    same_guard = op(dnf.packages, packages=("curl",), when=is_canary)
    assert guarded == same_guard
    assert guarded != unguarded
    assert len({unguarded, guarded, same_guard}) == 2


def test_when_failure_returns_first_failing_predicate_or_none():
    assert op(dnf.packages).when_failure(HOST) is None
    assert op(dnf.packages, when=is_canary).when_failure(HOST) is None
    web2 = Host.from_inventory(INVENTORY[1])
    assert op(dnf.packages, when=is_canary).when_failure(web2) is is_canary
    # Short-circuits in tuple order: the outermost failure is reported.
    request = op(dnf.packages, when=(never, is_canary))
    assert request.when_failure(HOST) is never


def test_compose_dedupe_distinguishes_requests_by_when():
    def same_op_different_guards(host: Host) -> Generator[OperationRequest]:
        yield op(dnf.packages, name="Install curl", packages=("curl",))
        yield op(
            dnf.packages, name="Install curl", packages=("curl",), when=is_canary
        )

    deploy = compose(same_op_different_guards, dedupe=True)
    assert len(list(deploy(HOST))) == 2


def test_compose_dedupe_collapses_identically_guarded_duplicates():
    def same_op_same_guard(host: Host) -> Generator[OperationRequest]:
        yield op(dnf.packages, name="Install curl", packages=("curl",), when=is_web)
        yield op(dnf.packages, name="Ensure curl", packages=("curl",), when=is_web)

    deploy = compose(same_op_same_guard, dedupe=True)
    requests = list(deploy(HOST))
    assert len(requests) == 1
    assert requests[0].name == "Install curl"


# --- sub_deploy: conjunction, outer-first, restrict-only ------------------------


def test_sub_deploy_when_prepends_policy_guards_to_inner_guards():
    def role(host: Host) -> Generator[OperationRequest]:
        yield op(dnf.packages, packages=("curl",), when=is_canary)

    (request,) = sub_deploy(role, when=is_web)(HOST)
    assert request.when == (is_web, is_canary)  # conjunction, outer first


def test_sub_deploy_when_default_leaves_requests_untouched():
    wrapped = sub_deploy(deploy_with_canary)
    assert list(wrapped(HOST)) == list(deploy_with_canary(HOST))


def test_sub_deploy_nested_when_guards_accumulate_outer_first():
    def role(host: Host) -> Generator[OperationRequest]:
        yield op(dnf.packages, packages=("curl",), when=is_canary)

    wrapped = sub_deploy(sub_deploy(role, when=is_web), when=never)
    (request,) = wrapped(HOST)
    assert request.when == (never, is_web, is_canary)


def test_sub_deploy_when_wrapper_is_pure_across_invocations():
    """Tuple concatenation (not fresh closures) keeps both phases equal."""
    wrapped = sub_deploy(deploy_with_canary, when=is_web, name_prefix="web")
    assert list(wrapped(HOST)) == list(wrapped(HOST))


# --- End to end: skips keep the schedule, the plan and the log aligned ----------


async def test_when_guard_skips_only_ineligible_hosts_end_to_end():
    plans, report, hosts = await run(
        INVENTORY, deploy_with_canary, host_factory=simulated_factory
    )

    # Same schedule on every host: the guarded request keeps its slot.
    assert [p.request.name for p in plans["web1"]] == [
        "Install baseline",
        "Install canary tooling",
    ]
    assert [p.request.name for p in plans["web2"]] == [
        "Install baseline",
        "Install canary tooling",
    ]
    skipped = plans["web2"][1]
    assert skipped.skipped is True
    assert skipped.changed is False
    assert skipped.reason == "when guard is_canary"
    assert skipped.commands == ()
    assert plans["web1"][1].skipped is False

    assert report is not None
    by_host = {entry.host: [] for entry in report.log}
    for entry in report.log:
        by_host[entry.host].append(entry)
    canary_entry = by_host["web2"][1]
    assert canary_entry.skipped is True
    assert canary_entry.changed is False
    assert canary_entry.commands == ()
    assert canary_entry.sudo is False  # nothing ran, so nothing was elevated

    # The skip is a third outcome, visible side by side in plan and log.
    assert "[skipped: when guard is_canary]" in format_plan(plans)
    assert "[skipped: when guard is_canary]" in format_log(report)
    assert report.changed is True  # the baseline still changed both hosts

    assert hosts[0].packages == {"curl": [""], "strace": [""]}
    assert hosts[1].packages == {"curl": [""]}


async def test_when_guard_skips_without_materializing_or_reading_facts():
    """An ineligible host never runs the operation body -- not even in prepare."""
    materialized: list[str] = []

    async def probe(ctx):
        materialized.append(ctx.host.name)
        yield  # pragma: no cover - the guard must prevent reaching this

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(probe, name="Guarded probe", when=never)

    plans, report, _hosts = await run(
        INVENTORY, deploy, host_factory=simulated_factory
    )
    assert materialized == []
    assert all(p.skipped for plan in plans.values() for p in plan)
    assert report is not None
    assert report.changed is False
    assert all(entry.skipped for entry in report.log)


async def test_when_guard_composes_with_sub_deploy_end_to_end():
    def role_canary(host: Host) -> Generator[OperationRequest]:
        yield op(dnf.packages, name="Install tooling", packages=("strace",))

    # Role-level guard over an unguarded role: web2 skips the whole role.
    deploy = compose(sub_deploy(role_canary, when=is_canary, name_prefix="canary"))
    plans, report, hosts = await run(
        INVENTORY, deploy, host_factory=simulated_factory
    )
    assert plans["web1"][0].skipped is False
    assert plans["web2"][0].skipped is True
    assert report is not None
    assert "[skipped: when guard is_canary]" in format_log(report)
    assert hosts[0].packages == {"strace": [""]}
    assert hosts[1].packages == {}


async def test_when_guard_skip_decision_is_visible_in_dry_run():
    plans, report, hosts = await run(
        INVENTORY, deploy_with_canary, host_factory=simulated_factory, dry=True
    )
    assert report is None
    assert plans["web2"][1].skipped is True
    assert "[skipped: when guard is_canary]" in format_plan(plans)
    assert hosts[0].packages == {}  # dry run changed nothing anywhere
    assert hosts[1].packages == {}


async def test_inline_lambda_guard_diverges_between_phases_and_raises():
    """A fresh lambda per phase is a different request value: the guard fires."""

    def deploy(host: Host) -> Generator[OperationRequest]:
        yield op(dnf.packages, packages=("curl",), when=lambda h: True)

    with pytest.raises(DeterminismError):
        await run(INVENTORY, deploy, host_factory=simulated_factory)