"""The ``server`` module: systemd facts, the ``service`` operation and waiting.

Fact side (design doc section 5): ``SystemdServiceState`` (a unit's
``ActiveState`` -- ``active``, ``inactive``, ``activating``, ... -- read by
``server.service`` to decide whether to start or stop, and polled by
``server.wait_service``) and ``SystemdServiceEnabled`` (whether a unit is
enabled on boot). Like every fact they are phase-unaware: each ``process``
coroutine just gathers the raw value.

Operation side (design doc section 6): ``server.service`` plus the
``SystemctlCommand`` descriptor it yields, and ``server.wait_service`` plus the
``WaitForFactCommand`` descriptor it yields. ``server.service`` mirrors
pyinfra's ``systemd.service`` for ``service``, ``running``, ``restarted``,
``enabled`` and ``daemon_reload``; pyinfra's ``reloaded``, ``command``,
``user_mode``, ``machine`` and ``user_name`` options are deliberately dropped.

``WaitForFactCommand`` is the *non-shell* wait descriptor: it carries a fact,
its arguments and a predicate, and its ``wait`` polls the fact (bypassing
the per-step ``FactCache``) until the predicate holds or the timeout
expires. It is generic -- any fact and predicate -- and lives here beside its
first consumer, ``server.wait_service``.
"""

import asyncio
import shlex
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from typing import Any

from ...commands import Command, WaitCommand, WaitTimeoutError
from ...core import OpContext
from ...hosts import Host

#: Unit suffixes systemd recognises; a bare name gets ``.service`` appended
#: (pyinfra's ``systemd.service`` normalization).
_UNIT_SUFFIXES = (
    ".service",
    ".socket",
    ".device",
    ".mount",
    ".automount",
    ".swap",
    ".target",
    ".path",
    ".timer",
    ".slice",
    ".scope",
)


def unit_name(service: str) -> str:
    """Normalize a service name to a full systemd unit name.

    ``httpd`` becomes ``httpd.service``; a name already carrying a systemd
    unit suffix (``logrotate.timer``, ``httpd.service``) is returned as is.
    Both the facts and the operations key on the normalized name, so the
    test framework's simulated state must use full unit names too.
    """
    if service.endswith(_UNIT_SUFFIXES):
        return service
    return f"{service}.service"


# --- Facts -------------------------------------------------------------------


class SystemdServiceState:
    """A unit's systemd ``ActiveState`` (``active``, ``inactive``, ...), or None.

    ``None`` means systemd reported nothing for the unit (``systemctl`` is
    missing or failed); an unknown unit reports ``inactive``, as systemd
    does. The value is the raw state string so a predicate (``is_active``)
    can decide what counts as "ready" -- ``activating`` is deliberately not
    ``active``, which is exactly what ``server.wait_service`` waits through.
    """

    @classmethod
    async def process(cls, host: Host, service: str) -> str | None:
        unit = unit_name(service)
        services = getattr(host, "services", None)
        if services is not None:
            # SimulatedHost seam: read the in-memory state directly.
            return services.get(unit)
        # Real transport: `systemctl show` over asyncssh. A failure exits
        # non-zero with empty stdout (check is off in run_shell), which maps
        # to None just like the simulated absent-unit case.
        stdout = await host.run_shell(
            f"systemctl show -p ActiveState --value {shlex.quote(unit)}"
        )
        return stdout.strip() or None


class SystemdServiceEnabled:
    """Whether a unit is enabled on boot.

    Mirrors pyinfra's ``SystemdEnabled``: the unit's ``UnitFileState`` is
    read and ``enabled`` or ``static`` count as enabled; anything else
    (``disabled``, ``masked``, an empty answer) does not.
    """

    #: ``UnitFileState`` values that count as enabled (pyinfra's
    #: ``SystemdEnabled.state_values``).
    ENABLED_STATES = ("enabled", "static")

    @classmethod
    async def process(cls, host: Host, service: str) -> bool:
        unit = unit_name(service)
        enabled_services = getattr(host, "enabled_services", None)
        if enabled_services is not None:
            # SimulatedHost seam: read the in-memory state directly.
            return unit in enabled_services
        # Real transport: `systemctl show` over asyncssh; a failure's empty
        # stdout maps to False.
        stdout = await host.run_shell(
            f"systemctl show -p UnitFileState --value {shlex.quote(unit)}"
        )
        return stdout.strip() in cls.ENABLED_STATES


# --- Operations --------------------------------------------------------------


@dataclass(frozen=True)
class SystemctlCommand(Command):
    """One ``systemctl <action> [<unit>]`` invocation.

    ``action`` is one of ``daemon-reload`` (no unit), ``start``, ``stop``,
    ``restart``, ``enable`` or ``disable``.
    """

    action: str
    unit: str | None = None  # None only for daemon-reload

    def shell(self) -> str:
        if self.unit is None:
            return f"systemctl {self.action}"
        return f"systemctl {self.action} {shlex.quote(self.unit)}"


async def service(
    ctx: OpContext,
    service: str,
    running: bool = True,
    restarted: bool = False,
    enabled: bool | None = None,
    daemon_reload: bool = False,
) -> AsyncGenerator[Command]:
    """Manage the state of a systemd unit (pyinfra ``systemd.service`` parity).

    + service: name of the unit (``httpd`` or ``httpd.service``; a bare name
      gets ``.service`` appended)
    + running: whether the unit should be running
    + restarted: whether to restart the unit (only if it is already running)
    + enabled: whether the unit should be enabled on boot; ``None`` leaves
      the boot setting alone
    + daemon_reload: reload the systemd daemon to read updated unit files
      before anything else

    ``daemon_reload`` and ``restarted`` are stateless (always yield when set,
    like pyinfra's non-idempotent operations); ``running`` and ``enabled``
    compare against the ``SystemdServiceState``/``SystemdServiceEnabled``
    facts and yield only when the unit differs from the request. A unit is
    considered running only when its ``ActiveState`` is ``active``, so a
    ``start`` is issued for an ``activating`` unit too. The commands need
    root: flag the request ``sudo=True``. pyinfra's ``reloaded``, ``command``,
    ``user_mode``, ``machine`` and ``user_name`` options are not supported.
    """
    unit = unit_name(service)
    reasons: list[str] = []

    if daemon_reload:
        ctx.changed = True
        reasons.append("daemon-reload")
        yield SystemctlCommand(action="daemon-reload")

    state = await ctx.facts.get(SystemdServiceState, unit)
    is_running = state == "active"

    if running and not is_running:
        ctx.changed = True
        reasons.append(f"{unit} not running ({state}), will start")
        yield SystemctlCommand(action="start", unit=unit)
    elif not running and is_running:
        ctx.changed = True
        reasons.append(f"{unit} running, will stop")
        yield SystemctlCommand(action="stop", unit=unit)
    else:
        reasons.append(f"{unit} already {'running' if running else 'stopped'}")

    # Only restart a unit that is already running (a stopped one was just
    # started above, or is meant to stay stopped).
    if restarted and is_running:
        ctx.changed = True
        reasons.append(f"{unit} will restart")
        yield SystemctlCommand(action="restart", unit=unit)

    if enabled is not None:
        is_enabled = await ctx.facts.get(SystemdServiceEnabled, unit)
        if enabled and not is_enabled:
            ctx.changed = True
            reasons.append(f"{unit} not enabled, will enable")
            yield SystemctlCommand(action="enable", unit=unit)
        elif not enabled and is_enabled:
            ctx.changed = True
            reasons.append(f"{unit} enabled, will disable")
            yield SystemctlCommand(action="disable", unit=unit)
        else:
            reasons.append(f"{unit} already {'enabled' if enabled else 'disabled'}")

    ctx.reason = "; ".join(reasons)


def is_active(state: str | None) -> bool:
    """The ``server.wait_service`` predicate: the unit's state is ``active``.

    A module-level function, not a lambda: the deploy generator is re-invoked
    per phase, and the predicate ends up in the yielded descriptor, so it
    must be the same object every time.
    """
    return state == "active"


@dataclass(frozen=True)
class WaitForFactCommand(WaitCommand):
    """Poll a fact until ``predicate(value)`` holds, or time out.

    The generic wait descriptor: ``fact_cls`` is any fact class (one with an
    ``async def process(host, ...)`` classmethod), ``args``/``kwargs`` are
    forwarded to it, and ``predicate`` decides when the gathered value counts
    as "condition met". Every field must be hashable and stable across phases
    -- pass module-level fact classes and predicates (never a lambda built
    inside a generator) and ``kwargs`` as a sorted tuple of items -- because
    the descriptor is compared by value in plans.

    Its loggable form is the ``__str__`` rendering ``command_shell`` falls
    back to; there is no ``shell()``.
    """

    fact_cls: type
    args: tuple[Any, ...] = ()
    kwargs: tuple[tuple[str, Any], ...] = ()  # sorted items, to stay hashable
    predicate: Callable[[Any], bool] = bool
    timeout: float = 300.0  # seconds before giving up
    interval: float = 5.0  # seconds between polls

    def __str__(self) -> str:
        args = ", ".join(repr(arg) for arg in self.args)
        predicate = getattr(self.predicate, "__name__", "predicate")
        return (
            f"wait for {self.fact_cls.__name__}({args}) <{predicate}> "
            f"every {self.interval}s up to {self.timeout}s"
        )

    async def wait(self, host: Host) -> str:
        """Poll ``fact_cls`` on ``host`` until the predicate holds.

        The fact is read through ``fact_cls.process`` directly, never through
        the per-step ``FactCache``: the cache would pin the first value and
        the loop would never observe a change. The deadline is checked
        *after* each poll, so a zero timeout still polls once (an
        already-met condition always succeeds) and ``asyncio.sleep`` only
        runs between attempts.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout
        kwargs = dict(self.kwargs)
        polls = 0
        while True:
            polls += 1
            value = await self.fact_cls.process(host, *self.args, **kwargs)
            if self.predicate(value):
                return f"condition met after {polls} poll(s)"
            if loop.time() >= deadline:
                raise WaitTimeoutError(
                    f"host {host.name}: {self} -- condition not met within "
                    f"{self.timeout}s (last value: {value!r})"
                )
            await asyncio.sleep(self.interval)


async def wait_service(
    ctx: OpContext,
    service: str,
    timeout: float = 300.0,
    interval: float = 5.0,
) -> AsyncGenerator[Command]:
    """Wait for a systemd unit to become ``active``.

    + service: name of the unit (``httpd`` or ``httpd.service``)
    + timeout: seconds to keep polling before raising ``WaitTimeoutError``
    + interval: seconds between polls

    Reads no facts and always yields: like pyinfra's ``server.wait``, a wait
    is not idempotent. Whether the unit is already active is discovered at
    apply time by the driver's poll loop, not at plan time -- a fact read
    here would reflect pre-deployment state in the prepare phase and
    post-start state in the apply phase, and branching on it would break the
    determinism guard. The plan therefore always shows the wait as a change;
    the apply log reports the wait descriptor that ran. A unit that never
    becomes active aborts the run with ``WaitTimeoutError``.
    """
    unit = unit_name(service)
    ctx.changed = True
    ctx.reason = f"wait for {unit} to be active"
    yield WaitForFactCommand(
        fact_cls=SystemdServiceState,
        args=(unit,),
        predicate=is_active,
        timeout=timeout,
        interval=interval,
    )


__all__ = [
    "SystemctlCommand",
    "SystemdServiceEnabled",
    "SystemdServiceState",
    "WaitForFactCommand",
    "is_active",
    "service",
    "unit_name",
    "wait_service",
]
