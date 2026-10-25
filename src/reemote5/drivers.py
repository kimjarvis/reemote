"""Drivers (design doc section 8).

The prepare phase collects the request sequence per host and materializes
each operation to compute ``(changed, reason)`` for plan/``--dry`` output.

The apply phase advances one generator per host in lockstep, recreating
each host's ``FactCache`` at every step so generator-body fact reads see
the effects of prior operations. Operations and facts never inspect a
phase flag; only the driver changes behavior between phases.

Command execution is asynchronous: ``send_command`` (and therefore
``run_apply`` and ``run``) are coroutines, preparing the apply path for a
real asyncssh-based transport. Fact gathering is asynchronous for the same
reason: a fact's ``process`` will run over asyncssh, so ``FactCache.get``,
the operations that await it, and ``materialize`` are coroutines too. The
whole prepare/apply path is therefore awaited end to end.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .commands import Command
from .core import FactCache, HostRuntime, OpContext, OperationRequest
from .hosts import Host


class DeterminismError(RuntimeError):
    """Raised when the apply-phase request sequence diverges from the plan.

    If a generator body branches on a mutable fact, the prepare and apply
    phases may produce different request sequences, breaking lockstep
    pairing (design doc section 11.1). The driver treats this as a hard
    error.
    """


@dataclass(frozen=True)
class PlannedOperation:
    """One materialized request in a host's plan."""

    request: OperationRequest
    changed: bool
    reason: str
    commands: tuple[Command, ...]


@dataclass(frozen=True)
class LogEntry:
    """One executed (or skipped) operation in the apply-phase run log."""

    host: str
    name: str
    changed: bool
    reason: str
    commands: tuple[Command, ...] = ()


@dataclass
class ApplyReport:
    log: list[LogEntry] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return any(entry.changed for entry in self.log)


async def materialize(host: Host, cache: FactCache, request: OperationRequest):
    """Fully materialize an operation's async command generator.

    Returns ``(ctx, commands)``. Materializing computes ``ctx.changed``
    (design doc section 6.3, cost of approach C); it is a coroutine because
    the operation awaits facts, which gather their values asynchronously.
    """
    ctx = OpContext(host=host, facts=cache)
    commands = [
        cmd async for cmd in request.func(ctx, *request.args, **request.kwargs)
    ]
    return ctx, commands


async def send_command(host: Host, cmd: Command) -> None:
    """Stand-in for pyinfra's SSH command execution.

    Declared ``async`` because the real implementation will run commands
    over asyncssh (rather than pyinfra's Paramiko + gevent stack), using
    the host's ``connection``/``session``/``sudo`` inventory data. The POC
    never connects anywhere: hosts providing an ``execute_command`` seam
    (the test framework's ``SimulatedHost``, injected via ``run``'s
    ``host_factory``) apply the command's simulated effect -- so subsequent
    fact reads observe the change -- and any other host hits the
    missing-transport boundary below.
    """
    execute = getattr(host, "execute_command", None)
    if execute is None:
        raise NotImplementedError(
            "real SSH command execution is not implemented: it will run "
            "over asyncssh using host.connection/host.session"
        )
    execute(cmd)


async def run_prepare(
    hosts: Iterable[Host], deploy_func: Callable
) -> tuple[list[HostRuntime], dict[str, list[PlannedOperation]]]:
    """Prepare phase: invoke each host's generator and materialize the plan.

    Returns ``(runtimes, plans)``. Fact reads go through the prepare-phase
    cache and reflect pre-deployment state; nothing is executed. A
    coroutine, because materializing awaits operations and their facts.
    """
    runtimes: list[HostRuntime] = []
    plans: dict[str, list[PlannedOperation]] = {}
    for host in hosts:
        cache = FactCache(host, phase="prepare")
        gen = deploy_func(host)
        requests = list(gen)
        plan = []
        for request in requests:
            ctx, commands = await materialize(host, cache, request)
            plan.append(
                PlannedOperation(
                    request=request,
                    changed=ctx.changed,
                    reason=ctx.reason,
                    commands=tuple(commands),
                )
            )
        # Re-invoke deploy(host) for the apply phase: generators cannot be
        # replayed once exhausted (design doc section 11.5).
        runtimes.append(HostRuntime(host, deploy_func(host), cache))
        plans[host.name] = plan
    return runtimes, plans


async def run_apply(
    runtimes: Iterable[HostRuntime],
    plans: dict[str, list[PlannedOperation]] | None = None,
) -> ApplyReport:
    """Apply phase: advance all host generators in lockstep.

    All hosts execute their Nth request before any host pulls request
    N+1. If ``plans`` is given, each pulled request sequence is checked
    against the plan; a mismatch raises ``DeterminismError``.

    A coroutine, because commands are executed via the async
    ``send_command``.
    """
    report = ApplyReport()
    active = [rt for rt in runtimes if not rt.done]
    pulled: dict[str, list[OperationRequest]] = {rt.host.name: [] for rt in active}

    while active:
        # Step 1: pull the next request from each active host's generator.
        # Recreate the fact cache so generator-body fact reads see prior ops.
        advanced = False
        for rt in active:
            rt.fact_cache = FactCache(rt.host, phase="apply")
            try:
                rt.current_request = next(rt.generator)
            except StopIteration:
                rt.done = True
                rt.current_request = None
            else:
                advanced = True
                pulled[rt.host.name].append(rt.current_request)

        if plans is not None and advanced:
            for rt in active:
                expected = plans[rt.host.name]
                got = pulled[rt.host.name]
                if len(got) > len(expected) or any(
                    g != e.request for g, e in zip(got, expected)
                ):
                    raise DeterminismError(
                        f"host {rt.host.name}: apply-phase request sequence "
                        f"diverges from the prepare-phase plan"
                    )

        # Step 2: execute each active host's current request.
        for rt in active:
            if rt.done or rt.current_request is None:
                continue
            request = rt.current_request
            ctx, commands = await materialize(rt.host, rt.fact_cache, request)
            rt.current_ctx = ctx
            sent: list[Command] = []
            if ctx.changed:
                for cmd in commands:
                    await send_command(rt.host, cmd)
                    sent.append(cmd)
            report.log.append(
                LogEntry(
                    host=rt.host.name,
                    name=request.name or request.func.__name__,
                    changed=ctx.changed,
                    reason=ctx.reason,
                    commands=tuple(sent),
                )
            )

        # Step 3: remove finished hosts.
        active = [rt for rt in active if not rt.done]

    return report


def format_plan(plans: dict[str, list[PlannedOperation]]) -> str:
    """Render plan output as shown in design doc section 10."""
    lines = []
    for host_name, plan in plans.items():
        for planned in plan:
            name = planned.request.name or planned.request.func.__name__
            if planned.changed:
                lines.append(f"{host_name}: {name:<24} [changed: {planned.reason}]")
            else:
                lines.append(f"{host_name}: {name:<24} [no change: {planned.reason}]")
    return "\n".join(lines)


def format_log(report: ApplyReport) -> str:
    """Render the apply-phase run log, including generated commands."""
    lines = []
    for entry in report.log:
        if entry.changed:
            lines.append(f"{entry.host}: {entry.name:<24} [changed: {entry.reason}]")
            for cmd in entry.commands:
                shell = cmd.shell() if hasattr(cmd, "shell") else str(cmd)
                lines.append(f"    $ {shell}")
        else:
            lines.append(f"{entry.host}: {entry.name:<24} [no change: {entry.reason}]")
    return "\n".join(lines)


async def run(
    inventory: Iterable[dict],
    deploy_func: Callable,
    *,
    host_factory: Callable[[dict], Host] = Host.from_inventory,
    dry: bool = False,
) -> tuple[dict[str, list[PlannedOperation]], ApplyReport | None, list[Host]]:
    """Top-level entry point: prepare, then apply (unless ``dry``).

    ``inventory`` is the user-supplied list of host dictionaries (see
    ``hosts.py``); each entry is materialized into a ``Host`` via
    ``host_factory``. The default factory builds plain library hosts; tests
    and examples inject a factory building ``SimulatedHost``\\ s (the
    test-framework class in ``tests/simulated_host.py``) so the simulation
    stays out of the library.

    Returns ``(plans, report, hosts)`` so callers can inspect the final
    host state. A coroutine: both the prepare phase (which materializes
    the plan) and the apply phase (which executes commands via the async
    ``send_command``) are awaited.
    """
    host_list = [host_factory(entry) for entry in inventory]
    runtimes, plans = await run_prepare(host_list, deploy_func)
    if dry:
        return plans, None, host_list
    report = await run_apply(runtimes, plans=plans)
    return plans, report, host_list


__all__ = [
    "ApplyReport",
    "DeterminismError",
    "LogEntry",
    "PlannedOperation",
    "format_log",
    "format_plan",
    "materialize",
    "run",
    "run_apply",
    "run_prepare",
    "send_command",
]
