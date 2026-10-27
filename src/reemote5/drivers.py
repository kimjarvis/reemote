"""Drivers (design doc section 8).

The prepare phase collects the request sequence per host and materializes
each operation to compute ``(changed, reason)`` for plan/``--dry`` output.

The apply phase advances one generator per host in lockstep, recreating
each host's ``FactCache`` at every step so generator-body fact reads see
the effects of prior operations. Operations and facts never inspect a
phase flag; only the driver changes behavior between phases.

Within each phase the per-host work is independent (each host owns its own
connection, generator and ``FactCache``), so it is issued concurrently across
hosts with ``asyncio.gather``: ``run_prepare`` gathers each host's whole
prepare, and ``run_apply`` gathers step 2 (materialize + send) for all active
hosts. The gather join is the barrier that preserves cross-host lockstep
ordering, and ``asyncio.gather`` returns results in input order so plans and
the run log stay deterministic. Commands within a single host still run
sequentially, since some command sequences are order-dependent.

Command execution is asynchronous: ``send_command`` (and therefore
``run_apply`` and ``run``) are coroutines, preparing the apply path for a
real asyncssh-based transport. Fact gathering is asynchronous for the same
reason: a fact's ``process`` will run over asyncssh, so ``FactCache.get``,
the operations that await it, and ``materialize`` are coroutines too. The
whole prepare/apply path is therefore awaited end to end.
"""

import asyncio
import shlex
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .commands import Command
from .core import FactCache, HostRuntime, OpContext, OperationRequest
from .hosts import Host, HostConnectionError, PrivilegeError, validate_inventory


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
    sudo: bool = False  # whether the operation's commands were sudo-elevated
    sudo_user: str | None = None  # run-as user from the host's privilege data
    su: bool = False  # whether the operation's commands were run via su
    su_user: str | None = None  # su target user from the host's privilege data


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
    commands = [cmd async for cmd in request.func(ctx, *request.args, **request.kwargs)]
    return ctx, commands


async def gather_fact(
    hosts: Iterable[Host], fact_cls: type, *args: Any, **kwargs: Any
) -> dict[str, Any]:
    """Gather one fact across all hosts concurrently.

    The driver-level fan-out that ``FactCache.get`` performs for a single
    host: it runs ``fact_cls.process`` for every host at once via
    ``asyncio.gather`` and returns a host-aligned mapping of ``host.name`` to
    that host's gathered value. Hosts are independent -- each owns its own
    connection and (in tests) its own simulated state -- so the per-host reads
    overlap on the network instead of running one host at a time.

    ``asyncio.gather`` preserves input order in its result list, so zipping the
    hosts with the gathered values keeps the mapping deterministic regardless
    of the order in which individual hosts finish. A dict keyed by host name is
    returned rather than a positional list so a caller can look a value up
    without re-deriving the inventory order (mirroring pyinfra's ``get_facts``,
    which returns ``{host: value}``).

    ``fact_cls`` is a fact class exposing an ``async def process(host, ...)``
    classmethod (e.g. ``DnfRepositories``); ``args``/``kwargs`` are forwarded to
    it verbatim, exactly as ``FactCache.get`` does. A coroutine, because each
    fact's ``process`` gathers its value asynchronously.
    """
    host_list = list(hosts)
    values = await asyncio.gather(
        *(fact_cls.process(host, *args, **kwargs) for host in host_list)
    )
    return {host.name: value for host, value in zip(host_list, values)}


def command_shell(
    cmd: Command,
    sudo: bool = False,
    sudo_user: str | None = None,
    su: bool = False,
    su_user: str | None = None,
) -> str:
    """Render the shell string a command descriptor runs as (the loggable form).

    Command descriptors stay dumb (design doc section 4.5); turning one into
    shell text is the driver's job. With ``sudo`` and/or ``su`` this is the
    elevated form the apply log shows; it is deliberately credential-free --
    when the host's ``privilege`` carries a password the real transport runs it
    under an askpass helper (see ``Host.run_shell_privileged``), plumbing this
    rendering never exposes so the password cannot leak into ``format_log``.
    ``send_command`` also calls it with the flags off to get the plain
    descriptor shell it hands to the transport.

    The two escalations combine in the fixed order ``sudo`` then ``su``: sudo
    is the outer prefix and ``su`` runs inside it, so a request flagged both
    renders as ``sudo [-u <sudo_user>] su <su_user> -c '<shell>'``.

    ``sudo_user``/``su_user`` are the run-as users taken from the host's
    inventory ``privilege`` section (never from the operation). ``sudo_user``
    is emitted as ``sudo -u <user>`` except that ``root`` -- sudo's own default
    target -- collapses to a bare ``sudo``. ``su_user`` is emitted as
    ``su <user> -c`` except that ``None``/``root`` -- su's default target --
    collapses to a bare ``su -c``.
    """
    shell = cmd.shell() if hasattr(cmd, "shell") else str(cmd)
    if su:
        # ``su`` defaults to root, so a ``su_user`` of ``None``/``root``
        # collapses to a bare ``su -c``; only a non-root target is named.
        if su_user and su_user != "root":
            shell = f"su {shlex.quote(su_user)} -c {shlex.quote(shell)}"
        else:
            shell = f"su -c {shlex.quote(shell)}"
    if not sudo:
        return shell
    # ``sudo`` defaults to root, so a ``sudo_user`` of ``root`` collapses to a
    # bare ``sudo``; only a non-root run-as user is emitted as ``-u <user>``.
    if sudo_user and sudo_user != "root":
        return f"sudo -u {shlex.quote(sudo_user)} {shell}"
    return f"sudo {shell}"


def _privilege_sudo_user(host: Host, sudo: bool) -> str | None:
    """The run-as user for an elevated command, from the host's privilege data.

    Returns ``None`` when the request is not elevated or the host specifies no
    ``sudo_user``. Sourced here -- from the inventory-backed ``Host.privilege``
    -- and never from the operation, so a deploy script cannot choose the
    run-as user.
    """
    if not sudo:
        return None
    privilege = getattr(host, "privilege", None) or {}
    return privilege.get("sudo_user")


def _privilege_su_user(host: Host, su: bool) -> str | None:
    """The ``su`` target user for a command, from the host's privilege data.

    The ``su`` counterpart of ``_privilege_sudo_user``: returns ``None`` when
    the request is not flagged ``su`` or the host specifies no ``su_user``.
    Sourced from the inventory-backed ``Host.privilege``, never from the
    operation.
    """
    if not su:
        return None
    privilege = getattr(host, "privilege", None) or {}
    return privilege.get("su_user")


async def send_command(
    host: Host, cmd: Command, *, sudo: bool = False, su: bool = False
) -> None:
    """Execute one command descriptor on a host.

    Two paths, chosen by the seams the host provides:

    - The test framework's ``SimulatedHost`` exposes an ``execute_command``
      seam; when present it applies the command's simulated effect (so
      subsequent fact reads observe the change) and nothing is sent anywhere.
      ``sudo``/``su`` are irrelevant here -- privilege has no in-memory effect
      -- so they are not forwarded. This is the path pytest always takes.
    - Any other host -- a plain library ``Host`` built from a real inventory
      entry -- runs the command's shell over asyncssh, using the host's
      ``connection`` data. An elevated request (``sudo`` and/or ``su``) goes
      through ``host.run_shell_privileged``, which applies the run-as users and
      any passwords (via askpass helpers) sourced from the inventory, in the
      fixed order ``sudo`` then ``su``; an unprivileged one goes through
      ``host.run_shell``. Either way a non-zero exit raises ``CommandError``
      (fail-fast), so a command that fails -- a permission error, a broken
      package transaction -- is never silently reported as applied. A failure
      sudo or su reports as its *own* authentication rejection (a wrong or
      missing password) raises ``PrivilegeError`` instead, which ``run_apply``
      aggregates across hosts exactly as ``run_prepare`` aggregates
      ``HostConnectionError``.
      This differs from pyinfra, which drives Paramiko under gevent.
    """
    execute = getattr(host, "execute_command", None)
    if execute is not None:
        execute(cmd)
        return
    base = command_shell(cmd)  # flags off -> the plain descriptor shell
    if sudo or su:
        await host.run_shell_privileged(base, sudo=sudo, su=su)
    else:
        await host.run_shell(base, check=True)


async def _prepare_host(
    host: Host, deploy_func: Callable
) -> tuple[HostRuntime, list[PlannedOperation]]:
    """Prepare one host: collect its request sequence and materialize the plan.

    Independent of every other host -- it owns its own prepare-phase
    ``FactCache`` and generator -- so ``run_prepare`` gathers this across the
    whole inventory to overlap the fact reads over SSH. Within a host the
    requests are materialized in order against pre-deployment state (prepare
    never sends commands, so nothing mutates between them).
    """
    cache = FactCache(host, phase="prepare")
    requests = list(deploy_func(host))
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
    return HostRuntime(host, deploy_func(host), cache), plan


async def run_prepare(
    hosts: Iterable[Host], deploy_func: Callable
) -> tuple[list[HostRuntime], dict[str, list[PlannedOperation]]]:
    """Prepare phase: invoke each host's generator and materialize the plan.

    Returns ``(runtimes, plans)``. Fact reads go through the prepare-phase
    cache and reflect pre-deployment state; nothing is executed. Each host is
    prepared concurrently via ``asyncio.gather`` (hosts are independent), and
    the results come back in input order, so ``runtimes`` and ``plans`` stay
    deterministic. A coroutine, because materializing awaits operations and
    their facts.

    Because the hosts connect concurrently, several may fail to be reached at
    once. Per-host ``HostConnectionError`` instances are therefore collected
    (via ``return_exceptions=True``) and merged into a single aggregate naming
    every unreachable host, so the caller learns about all of them together
    rather than only the first to fail. Any other exception is a genuine error
    and propagates unchanged.
    """
    host_list = list(hosts)
    results = await asyncio.gather(
        *(_prepare_host(host, deploy_func) for host in host_list),
        return_exceptions=True,
    )
    connection_errors: list[HostConnectionError] = []
    prepared: list[tuple[HostRuntime, list[PlannedOperation]]] = []
    for result in results:
        if isinstance(result, HostConnectionError):
            connection_errors.append(result)
        elif isinstance(result, BaseException):
            raise result
        else:
            prepared.append(result)
    if connection_errors:
        raise HostConnectionError.aggregate(connection_errors)

    runtimes: list[HostRuntime] = []
    plans: dict[str, list[PlannedOperation]] = {}
    for runtime, plan in prepared:
        runtimes.append(runtime)
        plans[runtime.host.name] = plan
    return runtimes, plans


async def _apply_host_step(rt: HostRuntime) -> LogEntry | None:
    """Execute one host's current request and return its log entry.

    Returns ``None`` when the host is idle this step (finished, or no request
    pulled). Commands within the host are sent sequentially -- some sequences
    are order-dependent -- so concurrency is only across hosts, provided by
    ``run_apply`` gathering this coroutine for every active host.
    """
    if rt.done or rt.current_request is None:
        return None
    request = rt.current_request
    ctx, commands = await materialize(rt.host, rt.fact_cache, request)
    rt.current_ctx = ctx
    sent: list[Command] = []
    if ctx.changed:
        for cmd in commands:
            await send_command(rt.host, cmd, sudo=request.sudo, su=request.su)
            sent.append(cmd)
    return LogEntry(
        host=rt.host.name,
        name=request.name or request.func.__name__,
        changed=ctx.changed,
        reason=ctx.reason,
        commands=tuple(sent),
        sudo=request.sudo,
        sudo_user=_privilege_sudo_user(rt.host, request.sudo),
        su=request.su,
        su_user=_privilege_su_user(rt.host, request.su),
    )


async def run_apply(
    runtimes: Iterable[HostRuntime],
    plans: dict[str, list[PlannedOperation]] | None = None,
) -> ApplyReport:
    """Apply phase: advance all host generators in lockstep.

    All hosts execute their Nth request before any host pulls request
    N+1. Within a step the per-host execution (materialize + send) runs
    concurrently across hosts via ``asyncio.gather``; the gather join is the
    barrier between steps, and results are collected in host order so the log
    stays deterministic. The request pull and the determinism check stay
    synchronous and complete before anything is sent. If ``plans`` is given,
    each pulled request sequence is checked against the plan; a mismatch
    raises ``DeterminismError``.

    Because a step's hosts execute concurrently, several may have their
    elevation rejected at once. Per-host ``PrivilegeError`` instances are
    therefore collected (via ``return_exceptions=True``) and merged into a
    single aggregate naming every host whose credentials failed, so one bad
    ``sudo_password`` is reported for the whole inventory at once -- the same
    treatment ``run_prepare`` gives ``HostConnectionError``. Any other
    exception is a genuine error and propagates unchanged, so a ``CommandError``
    or a bug still aborts the run.

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

        # Step 2: execute the current request on all active hosts
        # concurrently. asyncio.gather returns in input order, so the log stays
        # deterministic, and its join is the barrier holding lockstep ordering
        # before the next step. Per-host commands still run sequentially inside
        # ``_apply_host_step``.
        results = await asyncio.gather(
            *(_apply_host_step(rt) for rt in active), return_exceptions=True
        )
        privilege_errors: list[PrivilegeError] = []
        for result in results:
            if isinstance(result, PrivilegeError):
                privilege_errors.append(result)
            elif isinstance(result, BaseException):
                raise result
            elif result is not None:
                report.log.append(result)
        if privilege_errors:
            raise PrivilegeError.aggregate(privilege_errors)

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
                lines.append(
                    f"    $ {command_shell(cmd, entry.sudo, sudo_user=entry.sudo_user, su=entry.su, su_user=entry.su_user)}"
                )
        else:
            lines.append(f"{entry.host}: {entry.name:<24} [no change: {entry.reason}]")
    return "\n".join(lines)


async def run(
    inventory: list[dict],
    deploy_func: Callable,
    *,
    host_factory: Callable[[dict], Host] = Host.from_inventory,
    dry: bool = False,
) -> tuple[dict[str, list[PlannedOperation]], ApplyReport | None, list[Host]]:
    """Top-level entry point: prepare, then apply (unless ``dry``).

    ``inventory`` is the user-supplied list of host dictionaries (see
    ``hosts.py``). It is validated up front with ``validate_inventory`` --
    it must be a list of dictionaries, each carrying at least ``host`` and
    ``connection`` -- and raises ``InventoryError`` on bad input before any
    host is materialized. Each entry is then materialized into a ``Host``
    via ``host_factory``. The default factory builds plain library hosts;
    tests and examples inject a factory building ``SimulatedHost``\\ s (the
    test-framework class in ``tests/simulated_host.py``) so the simulation
    stays out of the library.

    Returns ``(plans, report, hosts)`` so callers can inspect the final
    host state. A coroutine: both the prepare phase (which materializes
    the plan) and the apply phase (which executes commands via the async
    ``send_command``) are awaited.
    """
    # Fail fast on a malformed inventory before materializing -- and
    # potentially connecting -- any host. The default factory validates each
    # entry again as it builds a Host; validating here enforces the contract
    # even for a custom ``host_factory`` that does not.
    validate_inventory(inventory)
    host_list = [host_factory(entry) for entry in inventory]
    try:
        runtimes, plans = await run_prepare(host_list, deploy_func)
        if dry:
            return plans, None, host_list
        report = await run_apply(runtimes, plans=plans)
        return plans, report, host_list
    finally:
        # ``run`` owns the hosts it materialized, so it tears down any real
        # asyncssh connections they opened (a no-op for SimulatedHost, which
        # never connects and has no ``aclose`` state to release).
        for host in host_list:
            aclose = getattr(host, "aclose", None)
            if aclose is not None:
                await aclose()


__all__ = [
    "ApplyReport",
    "DeterminismError",
    "LogEntry",
    "PlannedOperation",
    "command_shell",
    "format_log",
    "format_plan",
    "gather_fact",
    "materialize",
    "run",
    "run_apply",
    "run_prepare",
    "send_command",
]
