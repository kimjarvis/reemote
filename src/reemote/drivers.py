# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

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
import inspect
import shlex
from collections.abc import Callable, Generator, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from .actions import _aggregate_action_errors, _deploy_guard, _deploy_phase, _env_prefix
from .commands import Command, TransferCommand, WaitCommand
from .core import (
    DeployRequestError,
    FactCache,
    HostRuntime,
    OpContext,
    OperationError,
    OperationRequest,
    _account_request,
    _lost_requests,
    _RequestCounter,
    _tracked_requests,
    _unyielded_message,
)
from .hosts import (
    CommandNotFoundError,
    Host,
    HostConnectionError,
    PrivilegeError,
    validate_inventory,
)
from .stages import (
    ActionRequest,
    Barrier,
    CheckpointRequest,
    ControlRequest,
    FactRequest,
    RoleRecursionError,
    StageItem,
)


class DeterminismError(RuntimeError):
    """Raised when the apply-phase request sequence diverges from the plan.

    If a generator body branches on a mutable fact, the prepare and apply
    phases may produce different request sequences, breaking lockstep
    pairing (design doc section 11.1). The driver treats this as a hard
    error. The same applies to request *values*: a ``when`` predicate (or
    any other argument) constructed fresh on each phase invocation -- e.g.
    an inline ``lambda`` in a deploy body -- compares unequal across phases
    even when semantically identical, so guards must be stable function
    objects defined at module or composition level.
    """


@dataclass(frozen=True)
class PlannedOperation:
    """One materialized request in a host's plan.

    A ``skipped`` entry was not materialized at all: the request's ``when``
    guard failed on this host, so no facts were read and no commands were
    generated. ``changed`` is ``False`` and ``reason`` names the failing
    predicate.
    """

    request: OperationRequest
    changed: bool
    reason: str
    commands: tuple[Command, ...]
    skipped: bool = False  # True when the request's ``when`` guard failed


@dataclass(frozen=True)
class LogEntry:
    """One executed (or skipped) operation in the apply-phase run log.

    ``skipped`` marks an entry whose ``when`` guard failed on this host: it
    was neither materialized nor sent, ``changed`` is ``False`` and the
    privilege fields stay at their inert defaults (nothing ran, so nothing
    was elevated). This is a third outcome alongside ``changed`` and
    "no change": ineligible regardless of state, versus nothing to do.
    """

    host: str
    name: str
    changed: bool
    reason: str
    commands: tuple[Command, ...] = ()
    sudo: bool = False  # whether the operation's commands were sudo-elevated
    sudo_user: str | None = None  # run-as user from the host's privilege data
    su: bool = False  # whether the operation's commands were run via su
    su_user: str | None = None  # su target user from the host's privilege data
    doas: bool = False  # whether the operation's commands were run via doas
    doas_user: str | None = None  # doas target user from the host's privilege data
    # environment overrides from the request, in the normalized sorted-pair
    # form; ``format_log`` renders them as the same ``env K=V ...`` prefix the
    # transport ran, so the log line matches the wire
    env: tuple[tuple[str, str], ...] = ()
    skipped: bool = False  # True when the request's ``when`` guard failed
    # report path stamped by ``scoped()`` (design doc section 3.3). It never
    # reaches a host and is read only by ``format_waves``/checkpoint slicing to
    # group a line under its role, so it stays empty on the barrier-free
    # ``run()`` path -- which never scopes an entry.
    scope: tuple[str, ...] = ()


@dataclass
class ApplyReport:
    log: list[LogEntry] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return any(entry.changed for entry in self.log)


@dataclass
class WaveRuntime:
    """Per-host state for the wave loop: one generator, resumed many times.

    Unlike ``HostRuntime`` (which the two-phase ``run()`` path drives), a
    ``WaveRuntime`` holds no separate prepare/apply cursors: the loop pulls one
    item, and if it is a request materializes and applies it in the same step
    (design doc section 5.4), so there is no second pass to compare. ``pulled``
    is the request waiting to be stepped this iteration; ``parked`` is the
    barrier the generator yielded, held until every active host has parked and
    the rendezvous runs them all.
    """

    host: Host
    gen: Generator[StageItem, Any, Any]
    started: bool = False  # next(gen) on the first pull, gen.send(value) after
    pending: Any = None  # value to send into the generator on the next resume
    parked: Barrier | None = None  # barrier item waiting at the rendezvous
    pulled: OperationRequest | None = None  # request to plan/apply this step
    done: bool = False  # the generator returned; the host leaves the loop
    result: Any = None  # the generator's return value (a role's outputs)
    counter: _RequestCounter = field(default_factory=_RequestCounter)


@dataclass(frozen=True)
class StageRecord:
    """One line of the wave report: a request step, or a barrier.

    A step record carries the ``PlannedOperation`` it computed and the
    ``LogEntry`` it produced (``None`` in dry mode, which plans but never
    applies); a barrier record carries the barrier and its ``outcome``. Both
    kinds carry the ``wave`` number and the ``scope`` path so ``format_waves``
    can group them and ``apply_report`` can slice a checkpoint's wave.

    ``host`` is the host the record belongs to. It is redundant for a step that
    applied (its ``LogEntry`` already names the host) but is the only host name
    available for a dry plan line, where ``log`` is ``None`` -- so it is kept on
    every record for a uniform render.

    ``unknown`` marks a barrier dry mode parked on but never ran; it is a flag
    rather than a sentinel ``outcome`` because a fact or control callable may
    legitimately return ``None`` (design doc section 6.3).
    """

    wave: int
    scope: tuple[str, ...]
    host: str = ""
    planned: PlannedOperation | None = None
    log: LogEntry | None = None
    barrier: Barrier | None = None
    outcome: Any = None
    unknown: bool = False


@dataclass
class WaveReport:
    """Per-wave plan/log records plus the merged ``ApplyReport`` of every wave.

    ``records`` is the flat execution log of the run (steps then barriers,
    wave by wave); ``wave_ids`` lists the waves that actually recorded
    something, so an empty role emits nothing (design doc section 5.7).
    ``merged()`` folds every applied entry back into a plain ``ApplyReport`` --
    the analogue of ``run()``'s report -- so ``changed_hosts`` and any existing
    consumer work unchanged.
    """

    records: list[StageRecord] = field(default_factory=list)
    wave_ids: list[int] = field(default_factory=list)

    def close_wave(self, wave: int) -> None:
        """Record ``wave`` as closed, but only if it produced any record."""
        if any(record.wave == wave for record in self.records):
            self.wave_ids.append(wave)

    def mark_unknown(self, wave: int, runtimes: list["WaveRuntime"]) -> None:
        """Record every barrier of ``wave`` as planned-but-not-executed.

        Dry mode stops at the first rendezvous, so each parked barrier and
        everything after it is unknowable (design doc section 6.3).
        """
        for rt in runtimes:
            self.records.append(
                StageRecord(
                    wave=wave,
                    scope=rt.parked.scope,
                    host=rt.host.name,
                    barrier=rt.parked,
                    unknown=True,
                )
            )

    def apply_report(self, wave: int, scope: tuple[str, ...]) -> ApplyReport:
        """The applied entries of one wave, filtered to a ``scope`` prefix.

        This is the checkpoint-facing slice: a ``CheckpointRequest`` sends it
        back as its barrier result, so ``changed_hosts`` reads only the entries
        the checkpoint's own role emitted (design doc sections 4.3, 7.3).
        """
        report = ApplyReport()
        for record in self.records:
            if record.wave != wave or record.log is None:
                continue
            if record.scope[: len(scope)] == scope:
                report.log.append(record.log)
        return report

    def merged(self) -> ApplyReport:
        """All applied entries, in wave order -- the analogue of run()'s report."""
        report = ApplyReport()
        for record in self.records:
            if record.log is not None:
                report.log.append(record.log)
        return report

    def tree(self) -> str:
        """The scope/wave tree; a method alias for ``format_waves`` (section 7.2)."""
        return format_waves(self)


async def materialize(
    host: Host, cache: FactCache, request: OperationRequest, *, planning: bool = False
):
    """Fully materialize an operation's async command generator.

    Returns ``(ctx, commands)``. Materializing computes ``ctx.changed``
    (design doc section 6.3, cost of approach C); it is a coroutine because
    the operation awaits facts, which gather their values asynchronously.

    ``planning`` is threaded onto the ``OpContext`` for the phases that read
    state and never send a command -- a dry prepare, a ``dry=True`` wave step
    (issue #73). It is ``False`` by default, so an operation's missing-input
    check stays strict everywhere a materialization is followed by
    ``send_command``: only a preview may trade a ``ValueError`` for a plan line
    naming the input it could not see.

    An operation must be an async generator function. Anything else -- e.g.
    an action class passed to ``op()`` -- raises ``OperationError`` here rather
    than failing obscurely in the ``async for``: actions execute immediately
    and cannot run inside a deploy (see ``reemote.actions``).
    """
    if not inspect.isasyncgenfunction(request.func):
        raise OperationError(
            f"{getattr(request.func, '__name__', request.func)!r} is not an "
            "async-generator operation; actions cannot run inside a deploy "
            "(call gather_action before, between or after deploys instead)"
        )
    ctx = OpContext(host=host, facts=cache, planning=planning)
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
    without re-deriving the inventory order (the mapping is keyed by host
    name, ``{host: value}``).

    ``fact_cls`` is a fact class exposing an ``async def process(host, ...)``
    classmethod (e.g. ``DnfRepositories``); ``args``/``kwargs`` are forwarded to
    it verbatim, exactly as ``FactCache.get`` does. A coroutine, because each
    fact's ``process`` gathers its value asynchronously.

    Because the hosts are read concurrently, several may lack the command the
    fact runs at once. Per-host ``CommandNotFoundError`` instances (exit 127)
    are therefore collected (via ``return_exceptions=True``) and merged into
    a single aggregate naming every host missing the command -- the same
    treatment ``run_prepare`` gives ``HostConnectionError`` -- rather than
    reporting only the first to fail. Any other exception is a genuine error
    and propagates unchanged.
    """
    host_list = list(hosts)
    results = await asyncio.gather(
        *(fact_cls.process(host, *args, **kwargs) for host in host_list),
        return_exceptions=True,
    )
    missing: list[CommandNotFoundError] = []
    for result in results:
        if isinstance(result, CommandNotFoundError):
            missing.append(result)
        elif isinstance(result, BaseException):
            raise result
    if missing:
        raise CommandNotFoundError.aggregate(missing)
    return {host.name: value for host, value in zip(host_list, results)}


def command_shell(
    cmd: Command,
    sudo: bool = False,
    sudo_user: str | None = None,
    su: bool = False,
    su_user: str | None = None,
    doas: bool = False,
    doas_user: str | None = None,
    env: Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
) -> str:
    """Render the shell string a command descriptor runs as (the loggable form).

    Command descriptors stay dumb (design doc section 4.5); turning one into
    shell text is the driver's job. With ``doas``, ``sudo`` and/or ``su`` this
    is the elevated form the apply log shows; it is deliberately credential-free
    -- when the host's ``privilege`` carries a password the real transport runs
    it under an askpass helper (see ``Host.run_shell_privileged``), plumbing this
    rendering never exposes so the password cannot leak into ``format_log``.
    ``send_command`` also calls it with the flags off to get the plain
    descriptor shell it hands to the transport. A ``TransferCommand`` is never
    elevated -- it copies over the connection's scp channel as the login user --
    so its rendering is returned without any ``doas``/``sudo``/``su`` prefix
    even when the flags are set.

    The escalations combine in the fixed order ``doas`` then ``sudo`` then
    ``su``: ``doas`` is the outermost prefix and ``su`` the innermost, so a
    request flagged all three renders as ``doas -n [-u <doas_user>] sudo
    [-u <sudo_user>] su <su_user> -c '<shell>'``.

    ``sudo_user``/``su_user``/``doas_user`` are the run-as users taken from the
    host's inventory ``privilege`` section (never from the operation).
    ``sudo_user`` is emitted as ``sudo -u <user>`` except that ``root`` --
    sudo's own default target -- collapses to a bare ``sudo``. ``su_user`` is
    emitted as ``su <user> -c`` except that ``None``/``root`` -- su's default
    target -- collapses to a bare ``su -c``. ``doas_user`` is emitted as
    ``doas -n -u <user>`` except that ``None``/``root`` collapses to a bare
    ``doas -n`` (doas is passwordless, so no credential is ever rendered).

    ``env`` (a mapping or the normalized tuple of pairs an
    ``OperationRequest`` stores) is rendered by ``actions._env_prefix`` -- the
    same helper the ``Shell``/``Script`` actions use -- as a shell-quoted
    ``env K=V ...`` prefix placed *innermost*: immediately around the
    descriptor's own command text, inside every ``doas``/``sudo``/``su``
    wrapper, so the variables reach the command that runs instead of the
    outer shell (``sudo env http_proxy=<url> apt-get update``; under ``su``
    they ride inside the quoted payload). Unlike a password they *are* command
    text, so they appear in the log line -- which matches the actions' ``env``
    behaviour and is why a secret belongs in the inventory's ``privilege``
    section, never in ``env``. ``None``/empty adds no prefix. A descriptor that
    runs no shell text has no environment to set: ``TransferCommand`` returns
    before the prefix and ``WaitCommand`` (which polls a fact) skips it, so a
    silently-ignored ``env`` on those two is the documented contract rather
    than a rendering gap.
    """
    shell = cmd.shell() if hasattr(cmd, "shell") else str(cmd)
    # A transfer rides the connection's scp channel as the login user; the
    # ``doas``/``sudo``/``su`` flags never apply to it (``send_command`` ignores
    # them for a ``TransferCommand``), so the log rendering must not prefix them
    # either. An operation whose request is elevated for a *shell* command --
    # e.g. ``dnf.key``, which spools a key by scp then runs an elevated ``rpm
    # --import`` -- still transfers its bytes unprivileged, and the log shows
    # that truthfully rather than as a misleading ``sudo scp ...``. The same
    # reasoning drops ``env``: scp copies bytes, it runs no command whose
    # environment could be set.
    if isinstance(cmd, TransferCommand):
        return shell
    # A wait polls a fact through the descriptor's own coroutine and is
    # likewise unprivileged, so there is no command environment to prefix even
    # though the descriptor still renders as a loggable line.
    if not isinstance(cmd, WaitCommand):
        shell = f"{_env_prefix(env)}{shell}"
    if su:
        # ``su`` defaults to root, so a ``su_user`` of ``None``/``root``
        # collapses to a bare ``su -c``; only a non-root target is named.
        if su_user and su_user != "root":
            shell = f"su {shlex.quote(su_user)} -c {shlex.quote(shell)}"
        else:
            shell = f"su -c {shlex.quote(shell)}"
    if sudo:
        # ``sudo`` defaults to root, so a ``sudo_user`` of ``root`` collapses to
        # a bare ``sudo``; only a non-root run-as user is emitted as ``-u <user>``.
        if sudo_user and sudo_user != "root":
            shell = f"sudo -u {shlex.quote(sudo_user)} {shell}"
        else:
            shell = f"sudo {shell}"
    if doas:
        # ``doas`` defaults to root, so a ``doas_user`` of ``None``/``root``
        # collapses to a bare ``doas -n``; only a non-root target is named. doas
        # is passwordless, so ``-n`` (non-interactive) is always emitted and no
        # credential is ever rendered.
        if doas_user and doas_user != "root":
            shell = f"doas -n -u {shlex.quote(doas_user)} {shell}"
        else:
            shell = f"doas -n {shell}"
    return shell


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


def _privilege_doas_user(host: Host, doas: bool) -> str | None:
    """The ``doas`` target user for a command, from the host's privilege data.

    The ``doas`` counterpart of ``_privilege_sudo_user``: returns ``None`` when
    the request is not flagged ``doas`` or the host specifies no ``doas_user``.
    Sourced from the inventory-backed ``Host.privilege``, never from the
    operation.
    """
    if not doas:
        return None
    privilege = getattr(host, "privilege", None) or {}
    return privilege.get("doas_user")


async def send_command(
    host: Host,
    cmd: Command,
    *,
    sudo: bool = False,
    su: bool = False,
    doas: bool = False,
    env: Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
) -> None:
    """Execute one command descriptor on a host.

    Two paths, chosen by the seams the host provides:

    - The test framework's ``SimulatedHost`` exposes an ``execute_command``
      seam; when present it applies the command's simulated effect (so
      subsequent fact reads observe the change) and nothing is sent anywhere.
      ``sudo``/``su``/``doas`` are irrelevant here -- privilege has no
      in-memory effect -- so they are not forwarded, and ``env`` is no
      different: the simulation runs no shell, so there is no environment to
      set. This is the path pytest always takes.
    - Any other host -- a plain library ``Host`` built from a real inventory
      entry -- dispatches on the descriptor kind. A ``TransferCommand`` (e.g.
      ``files.put``'s ``PutCommand``, ``files.get``'s ``GetCommand``) copies
      over the connection's own transport via the descriptor's ``transfer``
      (asyncssh's scp), never through a shell, so the ``sudo``/``su``/``doas``
      escalation flags do not apply to it and are ignored. A ``WaitCommand``
      (e.g. ``server.wait_service``'s ``WaitForFactCommand``) likewise runs
      no shell: the driver awaits the descriptor's ``wait``, which polls the
      host until its condition holds or raises ``WaitTimeoutError``; the
      escalation flags are ignored for it too, and so is ``env``, for the same
      reason -- no command runs whose environment could be set. Anything else
      runs the command's shell over asyncssh, using the host's ``connection``
      data. An elevated request
      (``doas``, ``sudo`` and/or ``su``) goes
      through ``host.run_shell_privileged``, which applies the run-as users and
      any passwords (via askpass helpers) sourced from the inventory, in the
      fixed order ``doas`` then ``sudo`` then ``su``; an unprivileged one goes
      through ``host.run_shell``. Either way a non-zero exit raises
      ``CommandError`` (fail-fast), so a command that fails -- a permission
      error, a broken package transaction -- is never silently reported as
      applied. A failure doas, sudo or su reports as its *own* authentication
      rejection (a wrong or missing password, or a doas denial) raises
      ``PrivilegeError`` instead, which ``run_apply``
      aggregates across hosts exactly as ``run_prepare`` aggregates
      ``HostConnectionError``.

    ``env`` -- the request's environment overrides, as a mapping or the
    normalized tuple of pairs -- reaches the transport through the *same*
    rendering the log shows: ``command_shell`` is called once with the flags
    off and ``env`` set, which produces ``env K=V <shell>``, and the
    escalation then wraps that text (``host.run_shell_privileged`` adds
    ``doas``/``sudo``/``su`` around it). Threading the value through this one
    function -- rather than only the flag-on rendering ``format_log`` uses --
    is what keeps the loggable prefix and the wire prefix from diverging: the
    plan/log text and the executed text are built by the same code.
    """
    execute = getattr(host, "execute_command", None)
    if execute is not None:
        execute(cmd)
        return
    if isinstance(cmd, TransferCommand):
        await cmd.transfer(host)
        return
    if isinstance(cmd, WaitCommand):
        await cmd.wait(host)
        return
    # flags off -> the plain descriptor shell, with the request's environment
    # prefix; the escalation wrappers are added by run_shell_privileged below.
    base = command_shell(cmd, env=env)
    if sudo or su or doas:
        await host.run_shell_privileged(base, sudo=sudo, su=su, doas=doas)
    else:
        await host.run_shell(base, check=True)


async def _prepare_host(
    host: Host, deploy_func: Callable, *, planning: bool = False
) -> tuple[HostRuntime, list[PlannedOperation]]:
    """Prepare one host: collect its request sequence and materialize the plan.

    Independent of every other host -- it owns its own prepare-phase
    ``FactCache`` and generator -- so ``run_prepare`` gathers this across the
    whole inventory to overlap the fact reads over SSH. Within a host the
    requests are materialized in order against pre-deployment state (prepare
    never sends commands, so nothing mutates between them).

    ``planning`` marks a prepare that will never be applied -- the prepare of a
    ``dry=True`` ``run()`` -- and reaches every operation through
    ``materialize`` (issue #73). The prepare of a *full* run stays strict, so a
    missing input still fails before a single command is sent.

    A request whose ``when`` guard fails on this host is *not* materialized:
    the plan records a ``skipped`` entry without reading any facts (the
    operation might gather facts this host cannot even provide -- e.g. a dnf
    fact on a non-dnf host the guard exists to exclude). The guard reads
    static inventory data only, so the apply phase reaches the same decision.

    The deploy body itself is checked against the "yield requests" contract
    while it is consumed (see ``core.DeployRequestError``): the invocation runs
    inside a ``_RequestCounter`` so every ``op()`` call is tallied, each item the
    body produces is type-checked and charged as yielded, a body that returns no
    iterable at all (a ``def deploy`` that calls ``op()`` without ever yielding)
    is refused here rather than as a bare ``TypeError`` from ``list()``, and a
    request that was built but never handed over is reported once the body is
    exhausted. All of it happens before a single command is sent.
    """
    cache = FactCache(host, phase="prepare")
    counter = _RequestCounter()
    with _tracked_requests(counter):
        stream = deploy_func(host)
        if not isinstance(stream, Iterable):
            raise DeployRequestError(
                f"host {host.name}: the prepare-phase deploy body returned "
                f"{stream!r}, a {type(stream).__name__}, instead of a sequence "
                "of requests; a deploy is a generator whose body yields every "
                "request op() builds -- yield op(...), not op(...)"
            )
        requests = [
            _account_request(item, counter, host.name, "prepare") for item in stream
        ]
    if counter.unyielded:
        raise DeployRequestError(
            _unyielded_message(
                _lost_requests(host.name, counter.unyielded), "prepare"
            )
        )
    plan = []
    for request in requests:
        guard = request.when_failure(host)
        if guard is not None:
            plan.append(
                PlannedOperation(
                    request=request,
                    changed=False,
                    reason=f"when guard {guard.__name__}",
                    commands=(),
                    skipped=True,
                )
            )
            continue
        ctx, commands = await materialize(host, cache, request, planning=planning)
        plan.append(
            PlannedOperation(
                request=request,
                changed=ctx.changed,
                reason=ctx.reason,
                commands=tuple(commands),
            )
        )
    # Re-invoke deploy(host) for the apply phase: generators cannot be
    # replayed once exhausted (design doc section 11.5). The fresh counter
    # carries the same built-versus-yielded check into apply, where run_apply
    # installs it around each request pull.
    return (
        HostRuntime(host, deploy_func(host), cache, deploy_counter=_RequestCounter()),
        plan,
    )


@_deploy_phase
async def run_prepare(
    hosts: Iterable[Host], deploy_func: Callable, *, planning: bool = False
) -> tuple[list[HostRuntime], dict[str, list[PlannedOperation]]]:
    """Prepare phase: invoke each host's generator and materialize the plan.

    Returns ``(runtimes, plans)``. Fact reads go through the prepare-phase
    cache and reflect pre-deployment state; nothing is executed. Each host is
    prepared concurrently via ``asyncio.gather`` (hosts are independent), and
    the results come back in input order, so ``runtimes`` and ``plans`` stay
    deterministic. A coroutine, because materializing awaits operations and
    their facts.

    ``planning`` is forwarded to every ``_prepare_host`` and so to every
    operation, which lets an operation report a plan line for an input the
    deploy itself has not produced yet instead of raising (issue #73).
    ``run()`` passes it only for ``dry=True``.

    The ``_deploy_phase`` wrapper holds the in-deploy guard for the whole
    phase: a ``gather_action`` call from a deploy generator raises
    ``ActionInDeployError`` immediately (actions execute at once and would
    mutate hosts during prepare).

    Because the hosts connect concurrently, several may fail to be reached at
    once. Per-host ``HostConnectionError`` instances are therefore collected
    (via ``return_exceptions=True``) and merged into a single aggregate naming
    every unreachable host, so the caller learns about all of them together
    rather than only the first to fail. Any other exception is a genuine error
    and propagates unchanged.
    """
    host_list = list(hosts)
    results = await asyncio.gather(
        *(_prepare_host(host, deploy_func, planning=planning) for host in host_list),
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

    A request whose ``when`` guard fails on this host returns a ``skipped``
    log entry without materializing or sending anything -- the request still
    consumed its lockstep slot, so the shared schedule stays aligned.
    """
    if rt.done or rt.current_request is None:
        return None
    request = rt.current_request
    guard = request.when_failure(rt.host)
    if guard is not None:
        return LogEntry(
            host=rt.host.name,
            name=request.name or request.func.__name__,
            changed=False,
            reason=f"when guard {guard.__name__}",
            skipped=True,
        )
    # Resolve the tri-state escalation flags (``Privilege``) to the concrete
    # bools the execution layer speaks -- ``send_command``/``command_shell``/
    # ``LogEntry`` and the host's privilege builders all take a bool, and only
    # ``Privilege.ON`` elevates. By this point every ``sub_deploy`` wrapper has
    # run, so a still-``UNSET`` flag correctly means "do not elevate".
    sudo = request.sudo.elevates()
    su = request.su.elevates()
    doas = request.doas.elevates()
    ctx, commands = await materialize(rt.host, rt.fact_cache, request)
    rt.current_ctx = ctx
    sent: list[Command] = []
    if ctx.changed:
        for cmd in commands:
            await send_command(
                rt.host, cmd, sudo=sudo, su=su, doas=doas, env=request.env
            )
            sent.append(cmd)
    return LogEntry(
        host=rt.host.name,
        name=request.name or request.func.__name__,
        changed=ctx.changed,
        reason=ctx.reason,
        commands=tuple(sent),
        sudo=sudo,
        sudo_user=_privilege_sudo_user(rt.host, sudo),
        su=su,
        su_user=_privilege_su_user(rt.host, su),
        doas=doas,
        doas_user=_privilege_doas_user(rt.host, doas),
        # the request's normalized pairs, so the log renders the very prefix the
        # transport ran (and a skipped entry keeps the inert empty tuple)
        env=request.env,
    )


@_deploy_phase
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
    raises ``DeterminismError``. The ``_deploy_phase`` wrapper holds the
    in-deploy guard here too, so an action called from a generator body
    raises ``ActionInDeployError`` in apply exactly as it does in prepare.

    Because a step's hosts execute concurrently, several may have their
    elevation rejected at once. Per-host ``PrivilegeError`` instances are
    therefore collected (via ``return_exceptions=True``) and merged into a
    single aggregate naming every host whose credentials failed, so one bad
    ``sudo_password`` is reported for the whole inventory at once -- the same
    treatment ``run_prepare`` gives ``HostConnectionError``. Any other
    exception is a genuine error and propagates unchanged, so a ``CommandError``
    or a bug still aborts the run.

    The pull is also where the "yield requests" contract is enforced in apply
    (mirroring ``_prepare_host``): each pull runs inside the runtime's
    ``_RequestCounter`` so ``op()`` calls in the body are tallied, every pulled
    item is type-checked as an ``OperationRequest``, and a body that finishes
    having built more requests than it yielded raises ``DeployRequestError`` --
    aggregated across the hosts of the step, since a lockstep step pulls from
    every active host at once. A runtime the caller assembled itself, with no
    deploy accounting to speak of, still gets the type check.

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
        lost: list[str] = []
        for rt in active:
            rt.fact_cache = FactCache(rt.host, phase="apply")
            counter = rt.deploy_counter
            try:
                # Tally the body's op() calls against this host's counter for
                # the duration of the pull, so a request built and dropped is
                # visible when the generator runs out (see _prepare_host).
                with _tracked_requests(counter):
                    item = next(rt.generator)
            except StopIteration:
                rt.done = True
                rt.current_request = None
                if counter.unyielded:
                    lost.append(_lost_requests(rt.host.name, counter.unyielded))
            else:
                rt.current_request = _account_request(
                    item, counter, rt.host.name, "apply"
                )
                advanced = True
                pulled[rt.host.name].append(rt.current_request)

        if lost:
            raise DeployRequestError(_unyielded_message("; ".join(lost), "apply"))

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
            if planned.skipped:
                lines.append(f"{host_name}: {name:<24} [skipped: {planned.reason}]")
            elif planned.changed:
                lines.append(f"{host_name}: {name:<24} [changed: {planned.reason}]")
            else:
                lines.append(f"{host_name}: {name:<24} [no change: {planned.reason}]")
    return "\n".join(lines)


def format_log(report: ApplyReport) -> str:
    """Render the apply-phase run log, including generated commands.

    Each command line is the same ``command_shell`` rendering the transport was
    handed, so the privilege prefixes and the request's ``env K=V ...`` prefix
    appear exactly as executed. That is deliberate and it is why an ``env``
    value is *not* redacted here: like the actions' ``env`` (issue #4) it is
    ordinary command text, whereas a credential is never command text --
    ``sudo``/``su`` passwords stay in the askpass plumbing that this rendering
    deliberately does not see.
    """
    lines = []
    for entry in report.log:
        if entry.skipped:
            lines.append(f"{entry.host}: {entry.name:<24} [skipped: {entry.reason}]")
        elif entry.changed:
            lines.append(f"{entry.host}: {entry.name:<24} [changed: {entry.reason}]")
            for cmd in entry.commands:
                lines.append(
                    f"    $ {command_shell(cmd, entry.sudo, sudo_user=entry.sudo_user, su=entry.su, su_user=entry.su_user, doas=entry.doas, doas_user=entry.doas_user, env=entry.env)}"
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
    the pytest suites inject a factory building ``SimulatedHost`` objects (the
    test-framework class in ``tests/simulated_host.py``) so the simulation
    stays out of the library. The ``examples/`` scripts use the default
    factory and connect for real, so they mutate the hosts they target; the
    two exceptions are
    ``examples/demonstrations/composition/composed_group_tag_roles.py``, which
    injects a simulated factory, and
    ``examples/actions/action_in_deploy_guard.py``, whose guard fires during
    prepare, before any transport is opened.

    Returns ``(plans, report, hosts)`` so callers can inspect the final
    host state. A coroutine: both the prepare phase (which materializes
    the plan) and the apply phase (which executes commands via the async
    ``send_command``) are awaited.

    ``dry=True`` prepares in planning mode (issue #73): because nothing will
    be applied, an operation whose input a later request of the same deploy
    creates reports a plan line rather than raising. A full ``run()`` keeps its
    strict prepare, so a mistyped input still fails before anything mutates.
    """
    # Fail fast on a malformed inventory before materializing -- and
    # potentially connecting -- any host. The default factory validates each
    # entry again as it builds a Host; validating here enforces the contract
    # even for a custom ``host_factory`` that does not.
    validate_inventory(inventory)
    host_list = [host_factory(entry) for entry in inventory]
    try:
        runtimes, plans = await run_prepare(host_list, deploy_func, planning=dry)
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


async def run_waves(
    inventory: list[dict],
    deploy_func: Callable[[Host], Generator[StageItem, Any, Any]],
    *,
    host_factory: Callable[[dict], Host] = Host.from_inventory,
    dry: bool = False,
) -> tuple[WaveReport, list[Host]]:
    """Plan and apply a barrier-aware deploy, one wave per prepare/apply cycle.

    This is the resumable-deploy driver (design doc section 5). Each request is
    materialized and applied in the same step, before the next is pulled, so a
    wave needs no second invocation and no determinism comparison (section 6):
    there is no plan/apply window in which the body could diverge. When every
    active host has parked on a barrier (or finished), the parked barriers run
    concurrently under one ``asyncio.gather`` -- the rendezvous *is* the fan-out
    -- and each generator is resumed with its own sanitized result via
    ``gen.send(value)``.

    ``inventory`` and ``host_factory`` behave exactly as in ``run()``: the list
    is validated up front and each entry is materialized into a ``Host``. The
    driver owns those hosts and closes them once in its own ``finally``, so a
    barrier or a role must never close a connection (section 12, rule 4).

    ``dry=True`` is a one-wave preview: no barrier ever runs, and the first
    parked barrier is recorded ``unknown`` and stops the loop, because a
    barrier's outcome may depend on requests that were planned but never
    applied (section 6.3). The same reason makes a dry step a *planning*
    materialization (issue #73): an operation whose input an earlier request of
    this deploy stages reports a plan line instead of raising on the host that
    does not have it yet, so a spool-then-extract deploy previews in one wave.
    A ``dry=False`` step stays strict, and on the wave path the strict read is
    not a problem for that shape anyway -- requests apply one per step, so the
    spool has landed before the extract reads it.

    Returns ``(report, hosts)`` so a caller can render ``report.tree()``, fold
    ``report.merged()`` into the familiar ``ApplyReport``, and inspect the final
    host state. A coroutine: steps send commands and barriers gather facts,
    run actions and await control-side IO, all asynchronously.
    """
    validate_inventory(inventory)
    hosts = [host_factory(entry) for entry in inventory]
    report = WaveReport()
    try:
        runtimes = [WaveRuntime(h, deploy_func(h)) for h in hosts]
        wave = 0

        while runtimes:
            # 1. Pull one item per free host. A host is free unless it is
            #    parked on a barrier or has finished.
            for rt in runtimes:
                if rt.parked is None and not rt.done:
                    _pull(rt)

            # 2. Plan and apply this step's requests, one per host, in lockstep.
            stepped = [rt for rt in runtimes if rt.pulled is not None]
            if stepped:
                outcomes = await asyncio.gather(
                    *(_step(rt, dry) for rt in stepped), return_exceptions=True
                )
                _collect_steps(report, wave, stepped, outcomes)
                for rt in stepped:
                    rt.pulled = None

            # 3. Rendezvous: every active host is parked or done. Close the
            #    wave, then execute every parked barrier concurrently.
            if all(rt.done or rt.parked is not None for rt in runtimes):
                parked = [rt for rt in runtimes if rt.parked is not None]
                if parked and dry:
                    # A barrier's outcome may depend on requests that were
                    # planned but never applied, so the preview stops here.
                    report.mark_unknown(wave, parked)
                    report.close_wave(wave)
                    break
                report.close_wave(wave)
                if parked:
                    outcomes = await asyncio.gather(
                        *(_run_barrier(rt, report, wave) for rt in parked),
                        return_exceptions=True,
                    )
                    # Resume from the sanitised values, never from the raw
                    # outcomes: an aggregate error has already raised inside
                    # _collect_barriers, so every value here is data and may be
                    # sent into a generator.
                    values = _collect_barriers(report, wave, parked, outcomes)
                    for rt, value in zip(parked, values):
                        rt.pending, rt.parked = value, None
                    wave += 1

            runtimes = [rt for rt in runtimes if not rt.done]
    finally:
        # The driver owns the hosts: run()'s finally, moved here.
        for host in hosts:
            aclose = getattr(host, "aclose", None)
            if aclose is not None:
                await aclose()
    return report, hosts


def _pull(rt: WaveRuntime) -> None:
    """Resume one host's generator and classify the item it produced.

    Synchronous: resuming a generator does no IO. All the IO of a wave is in
    ``_step`` (the operations) and ``_run_barrier`` (the barriers), both of
    which the loop awaits -- so a pull can never interleave with a transport.

    The pull runs inside ``_stage_pull``, which installs both the request tally
    and the in-deploy guard for the duration of the body's execution. A body
    that runs out having built more items than it yielded raises the same
    lost-request ``DeployRequestError`` the legacy phases raise; a body that
    recurses without a base case (a role that yields from itself) raises
    ``RoleRecursionError``.
    """
    with _stage_pull(rt.counter):
        try:
            if not rt.started:
                item = next(rt.gen)
                rt.started = True
            else:
                item = rt.gen.send(rt.pending)
        except StopIteration as stop:
            rt.done, rt.result, rt.pending = True, stop.value, None
            # Same lost-item guard the legacy phases run when a body runs out:
            # a request or barrier built with a constructor and never yielded.
            if rt.counter.unyielded:
                raise DeployRequestError(
                    _unyielded_message(
                        _lost_requests(rt.host.name, rt.counter.unyielded), "wave"
                    )
                )
            return
        except RecursionError as exc:  # a role that depends on itself
            raise RoleRecursionError(
                f"host {rt.host.name}: role composition recursed without a base "
                "case; a role must not yield from itself (there is no "
                "de-duplication)"
            ) from exc

    rt.pending = None
    # One type check and one tally for both kinds of item.
    _account_stage(item, rt.counter, rt.host.name)
    if isinstance(item, OperationRequest):
        rt.pulled = item
    else:
        # _account_stage has already rejected everything outside the union.
        rt.parked = item


def _account_stage(
    item: Any, counter: _RequestCounter | None, host_name: str
) -> None:
    """Type-check one yielded item and charge it to the host's tally.

    The wave counterpart of ``core._account_request`` (design doc section 5.8):
    it accepts the widened union -- an ``OperationRequest`` or any
    :data:`~reemote.stages.Barrier` -- and charges ``counter.yielded`` for
    either, so ``counter.unyielded`` keeps meaning what it means today and a
    body that builds a request (or a barrier) with a constructor and then drops
    it is still caught when the generator runs out. ``counter`` is ``None`` for
    a hand-assembled ``WaveRuntime``, which keeps the type check while leaving
    the tally untracked -- exactly the compromise ``_account_request`` makes.

    Anything outside the union is a contract error raised here, before the loop
    can act on it: a role yields requests and barriers, nothing else.
    """
    if not isinstance(item, OperationRequest | Barrier):
        raise DeployRequestError(
            f"host {host_name}: the deploy body yielded {item!r}, a "
            f"{type(item).__name__}; a role yields OperationRequests and "
            "barriers (fact, act, checkpoint, control), nothing else"
        )
    if counter is not None:
        counter.yielded += 1


@contextmanager
def _stage_pull(counter: _RequestCounter) -> Iterator[None]:
    """Install the request tally AND the in-deploy guard around one pull.

    The guard must be active while the body runs (so a direct ``gather_action``
    call raises ``ActionInDeployError`` at the call site) and inactive while the
    driver executes a barrier -- which is why it wraps the pull only, never
    ``_step`` or ``_run_barrier`` (design doc section 5.3). ``_deploy_guard`` is
    the same context manager ``_deploy_phase`` holds for the whole of a legacy
    phase, so a direct ``gather_action`` call raises on the wave path exactly as
    it does there.
    """
    with _tracked_requests(counter), _deploy_guard():
        yield


async def _step(
    rt: WaveRuntime, dry: bool
) -> tuple[PlannedOperation, LogEntry | None]:
    """Materialize one host's pulled request once and apply it in the same step.

    This function is the whole determinism story for the wave path: ``materialize``
    is called once, its commands are sent in the same step, and there is no
    second materialization to compare (design doc section 5.4). A fresh
    ``FactCache`` per step -- exactly as ``run_apply`` does today -- lets an
    operation that reads a fact see the effect of the requests already applied.

    A request whose ``when`` guard fails returns a ``skipped`` plan and log
    entry without materializing or sending anything, so it is logged, not
    omitted, and never queues a handler. In ``dry`` mode the request is planned
    but not applied, so the log entry is ``None`` -- and the operation is
    materialized in planning mode, which is what lets a preview read an input a
    previous request of the same deploy would have created (issue #73).
    """
    request = rt.pulled
    guard = request.when_failure(rt.host)
    if guard is not None:
        # A skipped request is logged, not omitted: _apply_host_step returns a
        # skipped LogEntry today and format_log renders it as [skipped: ...].
        reason = f"when guard {guard.__name__}"
        return (
            PlannedOperation(
                request=request,
                changed=False,
                reason=reason,
                commands=(),
                skipped=True,
            ),
            LogEntry(
                host=rt.host.name,
                name=request.name or request.func.__name__,
                changed=False,
                reason=reason,
                skipped=True,
                scope=request.scope,
            ),
        )

    # A fresh cache per step, exactly as run_apply does today, so an operation
    # that reads a fact sees the effect of the requests already applied.
    cache = FactCache(rt.host, phase="wave")
    ctx, commands = await materialize(rt.host, cache, request, planning=dry)
    planned = PlannedOperation(
        request=request,
        changed=ctx.changed,
        reason=ctx.reason,
        commands=tuple(commands),
    )
    if dry:
        return planned, None

    # Resolve the tri-state escalation flags to concrete bools, and thread the
    # request's normalized ``env`` pairs into both the transport and the log so
    # the entry renders the very prefix that ran -- matching _apply_host_step.
    sudo = request.sudo.elevates()
    su = request.su.elevates()
    doas = request.doas.elevates()
    sent: list[Command] = []
    if ctx.changed:
        for cmd in commands:
            await send_command(
                rt.host, cmd, sudo=sudo, su=su, doas=doas, env=request.env
            )
            sent.append(cmd)
    entry = LogEntry(
        host=rt.host.name,
        name=request.name or request.func.__name__,
        changed=ctx.changed,
        reason=ctx.reason,
        commands=tuple(sent),
        sudo=sudo,
        sudo_user=_privilege_sudo_user(rt.host, sudo),
        su=su,
        su_user=_privilege_su_user(rt.host, su),
        doas=doas,
        doas_user=_privilege_doas_user(rt.host, doas),
        env=request.env,
        scope=request.scope,
    )
    return planned, entry


async def _run_barrier(rt: WaveRuntime, report: WaveReport, wave: int) -> Any:
    """Execute one parked host's barrier and return the value to send back.

    No ``gather_fact``/``gather_action`` call appears here: the rendezvous *is*
    the fan-out (design doc section 5.5). Each host calls its own fact or
    action, all parked hosts run concurrently under one ``asyncio.gather``, and
    the action's connection is **not** closed -- the driver owns hosts and
    closes them once in ``run_waves``'s ``finally``. This is why the loop calls
    ``action_cls.process(...)`` directly rather than ``_gather_action``, whose
    ``finally`` closes every host it touched.

    A checkpoint touches no host: its result is the just-closed wave's report,
    already sliced to the checkpoint's scope, so ``changed_hosts`` only ever
    reads ``.log``.
    """
    barrier = rt.parked
    if isinstance(barrier, FactRequest):
        return await barrier.fact_cls.process(rt.host, *barrier.args, **barrier.kwargs)
    if isinstance(barrier, ActionRequest):
        return await barrier.action_cls.process(
            rt.host, *barrier.args, **barrier.kwargs
        )
    if isinstance(barrier, ControlRequest):
        return await barrier.func(*barrier.args, **barrier.kwargs)
    if isinstance(barrier, CheckpointRequest):
        return report.apply_report(wave, barrier.scope)
    raise TypeError(f"unknown barrier {barrier!r}")


def _collect_steps(
    report: WaveReport,
    wave: int,
    stepped: list[WaveRuntime],
    outcomes: Iterable[Any],
) -> None:
    """Record one lockstep step: a plan line and a log line per host.

    ``asyncio.gather`` returns in input order, so zipping ``stepped`` with
    ``outcomes`` keeps the report host-ordered and gives each record its host
    name (needed to render a dry plan line, whose ``log`` is ``None``).
    ``PrivilegeError`` is aggregated across the step's hosts exactly as
    ``run_apply`` does; anything else aborts at once, so a ``CommandError`` or a
    bug still stops the run (design doc section 5.6).
    """
    privilege: list[PrivilegeError] = []
    for rt, outcome in zip(stepped, outcomes):
        if isinstance(outcome, BaseException):
            if isinstance(outcome, PrivilegeError):
                privilege.append(outcome)
            else:
                raise outcome
            continue
        planned, log = outcome
        report.records.append(
            StageRecord(
                wave=wave,
                scope=planned.request.scope,
                host=rt.host.name,
                planned=planned,
                log=log,
            )
        )
    if privilege:
        raise PrivilegeError.aggregate(privilege)


def _collect_barriers(
    report: WaveReport,
    wave: int,
    parked: list[WaveRuntime],
    outcomes: Iterable[Any],
) -> list[Any]:
    """Record each barrier's outcome and return the values to resume with.

    Barrier failures are aggregated across hosts by ``_aggregate_action_errors``
    -- the very helper ``gather_action``'s fan-out uses -- so the rendezvous
    reports ``HostConnectionError`` then ``ConnectionTimeoutError`` then
    ``PrivilegeError`` with identical precedence and re-raises anything else at
    once (design doc sections 5.6, 11.4). A non-zero **action** exit is data,
    not an exception, because ``Shell.process`` returns a ``Result``.

    Records are appended for every plain-value outcome first, then the errors
    are aggregated: on a raise the whole ``run_waves`` call unwinds and the
    report is discarded, so appending before the raise is not observable. Once
    ``_aggregate_action_errors`` returns, every outcome is a plain value and may
    be sent into the generators.
    """
    outcome_list = list(outcomes)
    for rt, outcome in zip(parked, outcome_list):
        if not isinstance(outcome, BaseException):
            report.records.append(
                StageRecord(
                    wave=wave,
                    scope=rt.parked.scope,
                    host=rt.host.name,
                    barrier=rt.parked,
                    outcome=outcome,
                )
            )
    _aggregate_action_errors(outcome_list)
    return outcome_list


def format_waves(report: WaveReport) -> str:
    """Group records by scope path, then by wave, then delegate per line.

    Each ``PlannedOperation`` renders through ``format_plan`` and each
    ``LogEntry`` through ``format_log``, so a request line is byte-identical to
    what the example prints today; only the indentation and the wave/barrier
    headings are new (design doc section 7.2). A barrier is printed inside the
    wave it closed, after that wave's request lines.

    Scope groups and waves are emitted in first-appearance order, so a composed
    program reads as one contiguous block per role, each in wave order, without
    sorting the global wave counter.
    """
    lines: list[str] = []
    scopes: list[tuple[str, ...]] = []
    for record in report.records:
        if record.scope not in scopes:
            scopes.append(record.scope)
    for scope in scopes:
        lines.append("/".join(scope) if scope else "(root)")
        scoped = [record for record in report.records if record.scope == scope]
        waves: list[int] = []
        for record in scoped:
            if record.wave not in waves:
                waves.append(record.wave)
        for wave in waves:
            lines.append(f"  wave {wave}")
            for record in scoped:
                if record.wave == wave:
                    lines.extend(_format_stage_record(record))
    return "\n".join(lines)


def _format_stage_record(record: StageRecord) -> list[str]:
    """Render one record as tree lines indented four spaces under its wave."""
    if record.barrier is not None:
        label = _barrier_label(record.barrier)
        return [f"    barrier: {label} \u2192 {_barrier_outcome(record)}"]
    if record.log is not None:
        block = format_log(ApplyReport(log=[record.log]))
    elif record.planned is not None:
        block = format_plan({record.host: [record.planned]})
    else:
        return []
    return [f"    {line}" for line in block.splitlines()]


def _barrier_label(barrier: Barrier) -> str:
    """The report label for a barrier: its ``name``, else a type-derived one."""
    if barrier.name:
        return barrier.name
    if isinstance(barrier, FactRequest):
        return barrier.fact_cls.__name__
    if isinstance(barrier, ActionRequest):
        return barrier.action_cls.__name__
    if isinstance(barrier, ControlRequest):
        return getattr(barrier.func, "__name__", "control")
    if isinstance(barrier, CheckpointRequest):
        return "checkpoint"
    return type(barrier).__name__


def _barrier_outcome(record: StageRecord) -> str:
    """The right-hand side of a barrier line: its result, or a dry-mode note."""
    if record.unknown:
        return "unknown until run"
    outcome = record.outcome
    if isinstance(record.barrier, CheckpointRequest) and isinstance(
        outcome, ApplyReport
    ):
        notified = len({entry.host for entry in outcome.log if entry.changed})
        return f"{notified} host{'s' if notified != 1 else ''} notified"
    return outcome if isinstance(outcome, str) else repr(outcome)


__all__ = [
    "ApplyReport",
    "DeployRequestError",
    "DeterminismError",
    "LogEntry",
    "PlannedOperation",
    "StageRecord",
    "WaveReport",
    "command_shell",
    "format_log",
    "format_plan",
    "format_waves",
    "gather_fact",
    "materialize",
    "run",
    "run_apply",
    "run_prepare",
    "run_waves",
    "send_command",
]
