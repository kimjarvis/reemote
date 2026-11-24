# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Tests for the wave-loop stage stream (design doc sections 3, 4, 5).

Covers ``reemote.stages`` and the ``run_waves`` driver: the four barrier
descriptors and their normalization, the ``scoped`` report-path transform (send
forwarding, return propagation, nesting), each barrier kind executed end to end
against ``SimulatedHost`` (a fact's value, an action's ``Result``, a control
coroutine's return, a checkpoint's sliced ``ApplyReport``), the wave/rendezvous
mechanics (lockstep steps, per-host asymmetry, wave numbering), ``dry`` mode
(plan-not-apply, the first barrier marked ``unknown``), the contract guards
(wrong item, unyielded barrier, self-recursion, ``compose``/``sub_deploy``
rejecting a barrier), error aggregation and propagation, and the ``WaveReport``
shape (``merged``/``tree``/empty).
"""

import dataclasses

import pytest

from reemote import (
    ActionRequest,
    ApplyReport,
    Barrier,
    CheckpointRequest,
    ControlRequest,
    DeployRequestError,
    FactRequest,
    FileExists,
    Host,
    HostConnectionError,
    OperationError,
    OperationRequest,
    RoleRecursionError,
    Shell,
    StageItem,
    act,
    changed_hosts,
    checkpoint,
    compose,
    control,
    fact,
    files,
    format_waves,
    op,
    run_waves,
    scoped,
    sub_deploy,
)
from tests.simulated_host import SimulatedHost


# A ``when`` guard over static inventory data: stable and module-level, so both
# the skip decision and the request value are identical across hosts.
def is_redhat(host: Host) -> bool:
    return host.family == "RedHat"


async def _noop() -> None:
    """A control coroutine returning ``None`` (a legal barrier result)."""


async def _tag(prefix: str, value: object) -> str:
    """A control coroutine echoing its arguments, to prove send-back."""
    return f"{prefix}:{value}"


class _DownFact:
    """A fact whose ``process`` always fails to connect (aggregation tests)."""

    @classmethod
    async def process(cls, host: Host):
        raise HostConnectionError([(host.name, "10.0.0.1", OSError("down"))])


# --- barrier descriptors: construction, normalization, frozen -----------------


def test_fact_constructor_builds_a_normalized_request():
    req = fact(FileExists, "/etc/motd", name="motd?")

    assert isinstance(req, FactRequest)
    assert req.fact_cls is FileExists
    assert req.args == ("/etc/motd",)
    assert req.kwargs == {}
    assert req.name == "motd?"
    assert req.scope == ()


def test_act_constructor_builds_an_action_request():
    req = act(Shell, command="echo hi", name="greet")

    assert isinstance(req, ActionRequest)
    assert req.action_cls is Shell
    assert req.kwargs == {"command": "echo hi"}
    assert req.name == "greet"


def test_checkpoint_constructor_takes_scope_keyword_only():
    assert checkpoint("done") == CheckpointRequest(name="done", scope=())
    assert checkpoint(scope=("apache",)).scope == ("apache",)


def test_control_constructor_builds_a_control_request():
    req = control(_tag, "arg", name="ctl")

    assert isinstance(req, ControlRequest)
    assert req.func is _tag
    assert req.args == ("arg",)
    assert req.name == "ctl"


def test_direct_construction_normalizes_sequences():
    # A list ``args``/``scope`` and an external ``kwargs`` dict are normalized,
    # so ``scoped``'s ``(label,) + item.scope`` always sees a tuple.
    req = FactRequest(fact_cls=FileExists, args=["/x"], kwargs={"a": 1}, scope=["r"])

    assert req.args == ("/x",)
    assert req.kwargs == {"a": 1}
    assert req.scope == ("r",)


def test_barriers_are_frozen():
    req = fact(FileExists, "/x")

    with pytest.raises(dataclasses.FrozenInstanceError):
        req.name = "other"


def test_barrier_union_matches_all_four_and_excludes_requests():
    for req in (
        fact(FileExists),
        act(Shell, command="x"),
        checkpoint(),
        control(_noop),
    ):
        assert isinstance(req, Barrier)
        assert isinstance(req, StageItem)

    request = op(files.file, path="x")
    assert isinstance(request, OperationRequest)
    assert isinstance(request, StageItem)
    assert not isinstance(request, Barrier)


# --- scoped: the report-path transform ----------------------------------------


def _probe(host: Host):
    """A deploy that parks on a fact and returns the value sent back."""
    value = yield fact(FileExists, "/etc/motd")
    return value


def test_scoped_stamps_scope_on_a_barrier():
    gen = scoped("apache", _probe)(Host(name="web1"))

    item = next(gen)
    assert isinstance(item, FactRequest)
    assert item.scope == ("apache",)


def test_scoped_forwards_send_and_propagates_the_return_value():
    gen = scoped("apache", _probe)(Host(name="web1"))
    next(gen)  # park on the fact

    # The value sent in reaches the inner generator's ``yield``, and the inner
    # ``return`` propagates back out through the wrapper.
    with pytest.raises(StopIteration) as stop:
        gen.send(True)
    assert stop.value.value is True


def test_scoped_nests_outermost_label_first():
    gen = scoped("outer", scoped("inner", _probe))(Host(name="web1"))

    assert next(gen).scope == ("outer", "inner")


def test_scoped_stamps_requests_without_rewriting_their_name():
    def deploy(host: Host):
        yield op(files.file, name="Write config", path="config")

    (item,) = list(scoped("apache", deploy)(Host(name="web1")))

    assert isinstance(item, OperationRequest)
    assert item.scope == ("apache",)
    # ``changed_hosts`` matches on ``name``, so ``scoped`` must not rewrite it.
    assert item.name == "Write config"


# --- run_waves: the inventory and host factory --------------------------------

INVENTORY = [
    {"host": "web1", "family": "RedHat", "connection": {}},
    {"host": "web2", "family": "Ubuntu", "connection": {}},
]


def host_factory(entry: dict) -> Host:
    """web1 already has ``/etc/motd``; both answer the ``echo hi`` action."""
    host = SimulatedHost.from_inventory(entry)
    if entry["host"] == "web1":
        host.existing_files.add("/etc/motd")
    host.shell_results["echo hi"] = {
        "exit_status": 0,
        "stdout": "hi\n",
        "stderr": "",
    }
    return host


def _apache_write(host: Host):
    yield op(files.file, name="Write apache", path="apache.conf")


def _nginx_write(host: Host):
    yield op(files.file, name="Write nginx", path="nginx.conf")


# --- run_waves: each barrier kind sends its result back -----------------------


async def test_fact_barrier_sends_the_value_back_into_the_generator():
    def deploy(host: Host):
        exists = yield fact(FileExists, "/etc/motd")
        # The sent-back value drives control flow: tag a control barrier with it.
        yield control(_tag, "motd", exists)

    report, _hosts = await run_waves(INVENTORY, deploy, host_factory=host_factory)

    facts = {
        r.host: r.outcome for r in report.records if isinstance(r.barrier, FactRequest)
    }
    assert facts == {"web1": True, "web2": False}
    # The control barrier's argument used the fact value, proving send-back.
    controls = {
        r.host: r.outcome
        for r in report.records
        if isinstance(r.barrier, ControlRequest)
    }
    assert controls == {"web1": "motd:True", "web2": "motd:False"}


async def test_act_barrier_sends_the_result_back():
    def deploy(host: Host):
        yield act(Shell, command="echo hi", name="greet")

    report, hosts = await run_waves(INVENTORY, deploy, host_factory=host_factory)

    outcomes = [
        r.outcome for r in report.records if isinstance(r.barrier, ActionRequest)
    ]
    assert outcomes == [
        {"exit_status": 0, "stdout": "hi\n", "stderr": ""},
        {"exit_status": 0, "stdout": "hi\n", "stderr": ""},
    ]
    # The action really ran on each host, exactly once.
    for host in hosts:
        assert host.ran_shells == ["echo hi"]


async def test_a_non_zero_action_exit_is_data_not_an_exception():
    def factory(entry: dict) -> Host:
        host = SimulatedHost.from_inventory(entry)
        host.shell_results["false"] = {
            "exit_status": 3,
            "stdout": "",
            "stderr": "boom",
        }
        return host

    def deploy(host: Host):
        yield act(Shell, command="false")

    report, _hosts = await run_waves(INVENTORY, deploy, host_factory=factory)

    outcomes = [
        r.outcome for r in report.records if isinstance(r.barrier, ActionRequest)
    ]
    assert [o["exit_status"] for o in outcomes] == [3, 3]


async def test_a_control_barrier_runs_once_per_parked_host():
    calls: list[str] = []

    async def counter(name: str) -> int:
        calls.append(name)
        return len(calls)

    def deploy(host: Host):
        yield control(counter, host.name)

    _report, _hosts = await run_waves(INVENTORY, deploy, host_factory=host_factory)

    # N hosts parked on the barrier means N controller-side calls.
    assert sorted(calls) == ["web1", "web2"]


async def test_checkpoint_sends_back_the_wave_apply_report():
    def deploy(host: Host):
        yield op(files.file, name="Write config", path="config")
        yield checkpoint("config done")

    report, _hosts = await run_waves(INVENTORY, deploy, host_factory=host_factory)

    checkpoints = [
        r.outcome for r in report.records if isinstance(r.barrier, CheckpointRequest)
    ]
    assert len(checkpoints) == 2
    wave_report = checkpoints[0]
    # The checkpoint's result IS an ApplyReport, so changed_hosts reads it as-is.
    assert isinstance(wave_report, ApplyReport)
    assert changed_hosts(wave_report, {"Write config"}) == frozenset({"web1", "web2"})


async def test_checkpoint_slices_the_wave_report_to_its_scope():
    def deploy(host: Host):
        yield from scoped("apache", _apache_write)(host)
        yield from scoped("nginx", _nginx_write)(host)
        yield checkpoint("apache done", scope=("apache",))

    report, _hosts = await run_waves(INVENTORY, deploy, host_factory=host_factory)

    wave_report = next(
        r.outcome for r in report.records if isinstance(r.barrier, CheckpointRequest)
    )
    # Only the apache-scoped entries survive the slice: one per host.
    assert len(wave_report.log) == 2
    assert all(entry.scope == ("apache",) for entry in wave_report.log)
    assert all(entry.name == "Write apache" for entry in wave_report.log)


# --- run_waves: wave and rendezvous mechanics ---------------------------------


async def test_requests_and_barriers_are_numbered_by_wave():
    def deploy(host: Host):
        yield op(files.file, name="before", path="a")
        yield fact(FileExists, "/etc/motd")
        yield op(files.file, name="after", path="b")

    report, _hosts = await run_waves(INVENTORY, deploy, host_factory=host_factory)

    waves: dict[str, set[int]] = {}
    for record in report.records:
        if record.log is not None:
            waves.setdefault(record.log.name, set()).add(record.wave)
        elif record.barrier is not None:
            waves.setdefault("barrier", set()).add(record.wave)
    # The barrier closes wave 0; the request after it lands in wave 1.
    assert waves["before"] == {0}
    assert waves["barrier"] == {0}
    assert waves["after"] == {1}
    assert report.wave_ids == [0, 1]


async def test_hosts_may_finish_at_different_waves():
    def deploy(host: Host):
        yield fact(FileExists, "/etc/motd")
        if host.name == "web2":
            yield op(files.file, name="extra", path="extra")

    report, _hosts = await run_waves(INVENTORY, deploy, host_factory=host_factory)

    stepped = [r for r in report.records if r.log is not None]
    # Only web2 emitted a request after the rendezvous; web1 finished at once.
    assert {r.host for r in stepped} == {"web2"}
    assert [r.log.name for r in stepped] == ["extra"]


async def test_a_failed_when_guard_is_logged_not_omitted():
    def deploy(host: Host):
        yield op(files.file, name="redhat only", path="r", when=is_redhat)

    report, _hosts = await run_waves(INVENTORY, deploy, host_factory=host_factory)

    entries = {r.log.host: r.log for r in report.records if r.log is not None}
    # web1 is RedHat: the request applies. web2 is not: it is skipped, logged.
    assert entries["web1"].changed is True
    assert entries["web1"].skipped is False
    assert entries["web2"].changed is False
    assert entries["web2"].skipped is True
    assert "is_redhat" in entries["web2"].reason


async def test_scoped_composition_groups_the_report_by_role():
    def program(host: Host):
        yield from scoped("apache", _apache_write)(host)

    report, _hosts = await run_waves(INVENTORY, program, host_factory=host_factory)

    assert all(record.scope == ("apache",) for record in report.records)
    tree = report.tree()
    assert "apache" in tree
    assert "(root)" not in tree


# --- run_waves: dry mode is a one-wave preview --------------------------------


async def test_dry_run_plans_without_applying_and_marks_the_barrier_unknown():
    def deploy(host: Host):
        yield op(files.file, name="Write config", path="dryfile")
        yield act(Shell, command="echo hi")

    report, hosts = await run_waves(
        INVENTORY, deploy, host_factory=host_factory, dry=True
    )

    steps = [r for r in report.records if r.planned is not None]
    assert steps and all(r.log is None for r in steps)
    unknown = [r for r in report.records if r.unknown]
    assert len(unknown) == 2  # one per parked host
    assert all(isinstance(r.barrier, ActionRequest) for r in unknown)
    # Nothing ran and nothing was written: the preview stops at the barrier.
    for host in hosts:
        assert host.ran_shells == []
        assert "dryfile" not in host.existing_files
    assert report.merged().log == []


async def test_dry_run_with_a_leading_barrier_previews_nothing():
    def deploy(host: Host):
        yield fact(FileExists, "/etc/motd")
        yield op(files.file, name="after", path="x")

    report, _hosts = await run_waves(
        INVENTORY, deploy, host_factory=host_factory, dry=True
    )

    # The first item is a barrier, so dry stops at once: no request is planned.
    assert all(r.planned is None for r in report.records)
    assert [r.unknown for r in report.records] == [True, True]
    assert report.merged().log == []


# --- contract guards ----------------------------------------------------------


def test_compose_rejects_a_barrier():
    def deploy(host: Host):
        yield fact(FileExists, "/etc/motd")

    with pytest.raises(
        DeployRequestError, match=r"compose\(\) received a FactRequest"
    ):
        list(compose(deploy)(Host(name="web1")))


def test_sub_deploy_rejects_a_barrier():
    def deploy(host: Host):
        yield checkpoint("done")

    with pytest.raises(
        DeployRequestError, match=r"sub_deploy\(\) received a CheckpointRequest"
    ):
        list(sub_deploy(deploy)(Host(name="web1")))


async def test_yielding_a_non_stage_item_is_a_contract_error():
    def deploy(host: Host):
        yield "not a request"

    with pytest.raises(
        DeployRequestError, match="a role yields OperationRequests and barriers"
    ):
        await run_waves(INVENTORY, deploy, host_factory=host_factory)


async def test_a_built_but_unyielded_barrier_is_reported():
    def deploy(host: Host):
        yield op(files.file, name="x", path="x")
        act(Shell, command="echo hi")  # built, never yielded

    with pytest.raises(DeployRequestError, match="lost 1 built request"):
        await run_waves(INVENTORY, deploy, host_factory=host_factory)


async def test_a_self_recursive_role_raises_role_recursion_error():
    def recursive(host: Host):
        yield from recursive(host)

    with pytest.raises(RoleRecursionError, match="recursed without a base case"):
        await run_waves(INVENTORY, recursive, host_factory=host_factory)


# --- error aggregation and propagation ----------------------------------------


async def test_connection_failures_aggregate_across_hosts():
    def deploy(host: Host):
        yield fact(_DownFact)

    with pytest.raises(HostConnectionError) as excinfo:
        await run_waves(INVENTORY, deploy, host_factory=host_factory)

    assert set(excinfo.value.host_names) == {"web1", "web2"}


async def test_a_plain_barrier_error_propagates_unchanged():
    async def boom() -> None:
        raise ValueError("control side blew up")

    def deploy(host: Host):
        yield control(boom)

    with pytest.raises(ValueError, match="control side blew up"):
        await run_waves(INVENTORY, deploy, host_factory=host_factory)


async def test_an_operation_error_in_a_step_aborts_the_run():
    def deploy(host: Host):
        # ``Shell`` is an action, not an async-generator operation.
        yield op(Shell, command="echo hi")

    with pytest.raises(OperationError):
        await run_waves(INVENTORY, deploy, host_factory=host_factory)


# --- WaveReport shape ---------------------------------------------------------


async def test_an_empty_deploy_emits_no_waves():
    def deploy(host: Host):
        return
        yield  # pragma: no cover - the presence of ``yield`` makes it a generator

    report, _hosts = await run_waves(INVENTORY, deploy, host_factory=host_factory)

    assert report.records == []
    assert report.wave_ids == []
    assert report.merged().log == []
    assert report.tree() == ""


async def test_merged_folds_every_applied_entry_in_wave_order():
    def deploy(host: Host):
        yield op(files.file, name="one", path="a")
        yield fact(FileExists, "/etc/motd")
        yield op(files.file, name="two", path="b")

    report, _hosts = await run_waves(INVENTORY, deploy, host_factory=host_factory)

    merged = report.merged()
    assert isinstance(merged, ApplyReport)
    # Two requests across two hosts, in wave order; barriers carry no log entry.
    assert [entry.name for entry in merged.log] == ["one", "one", "two", "two"]


async def test_the_tree_groups_by_scope_then_wave():
    def deploy(host: Host):
        yield from scoped("apache", _apache_write)(host)
        yield fact(FileExists, "/etc/motd", name="probe")

    report, _hosts = await run_waves(INVENTORY, deploy, host_factory=host_factory)

    tree = report.tree()
    assert tree == format_waves(report)
    assert "apache" in tree  # the scoped request group
    assert "(root)" in tree  # the root-scoped barrier group
    assert "wave 0" in tree
    assert "barrier: probe" in tree
