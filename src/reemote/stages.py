# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """Stage stream types for the wave-loop driver (design doc sections 3, 4).
#
# A role is a deploy factory: an ordinary function that closes over resolved
# variables and returns a deploy generator.  The generator yields two kinds of
# item -- an :class:`~reemote.core.OperationRequest` (exactly what a deploy
# yields today) and a *barrier* (one of four frozen descriptors that park the
# generator until the driver executes them and resumes it with the result via
# ``gen.send(value)``).
#
# The four barrier types exist because they are the four things a **sync**
# generator body cannot do itself:
#
# | Barrier                    | Constructor       | Sends back                         | Host contact |
# |----------------------------|-------------------|------------------------------------|--------------|
# | :class:`FactRequest`       | :func:`fact`      | the fact's value                   | one host     |
# | :class:`ActionRequest`     | :func:`act`       | ``Result`` (or action-specific)    | one host     |
# | :class:`CheckpointRequest` | :func:`checkpoint`| the wave's ``ApplyReport``, sliced | none         |
# | :class:`ControlRequest`    | :func:`control`   | the coroutine's return value       | controller   |
#
# :data:`Barrier` is the PEP 604 union of the four, :data:`StageItem` widens it
# with ``OperationRequest``, and :data:`Deploy` is the per-host generator
# signature a role factory returns.  :func:`scoped` tags every item a deploy
# yields with a report path (the ``scope`` field), which is how ``yield from``
# composition keeps role boundaries visible in the report without rewriting
# ``OperationRequest.name``.
#
# Each constructor charges the deploy body's built-versus-yielded counter (the
# same :class:`~reemote.core._RequestCounter` that catches a lost ``op()``
# request), so a barrier built and then dropped is reported exactly like a lost
# request when the generator runs out.
#
# :class:`RoleRecursionError` replaces the bare ``RecursionError`` a role's
# ``yield from`` chain raises when it depends on itself -- there is no
# de-duplication and no static graph to check, so the driver catches the
# recursion at the pull site and re-raises with a message naming the mistake.
# """

from collections.abc import Awaitable, Callable, Generator
from dataclasses import dataclass, field, replace
from typing import Any

from .core import OperationRequest, _charge_built
from .hosts import Host


class RoleRecursionError(RecursionError):
    """Raised when a role's ``yield from`` chain recurses without a base case.

    A role must not depend on itself: there is no de-duplication and no static
    dependency graph, so a cycle degrades to a ``RecursionError`` caught by the
    driver at the pull site (design doc section 5.3) and re-raised as this
    error, naming the host and the mistake.  Subclasses ``RecursionError`` so
    existing handlers keep working.
    """


@dataclass(frozen=True)
class FactRequest:
    """A barrier that reads remote state into the generator's control flow.

    Attributes:
        fact_cls: The fact class exposing ``async def process(host, ...)``.
        args: Positional arguments forwarded to ``process``.
        kwargs: Keyword arguments forwarded to ``process``.
        name: Human-readable label for the report tree.
        scope: Report path set by :func:`scoped`; never reaches a host.
    """

    # The driver executes ``await fact_cls.process(host, *args, **kwargs)`` for
    # the parked host and sends the value back via ``gen.send()``.  Every parked
    # host runs its own fact concurrently at the rendezvous, reproducing
    # ``gather_fact``'s fan-out without a host set (design doc section 4.1).
    #
    # A fact is a **read**; it is executed, never simulated.  ``dry`` stops
    # before the first barrier, so no fact runs there.  No ``FactCache`` is
    # involved -- a barrier is an explicit, fresh read, not an operation's
    # internal materialization-time read.


    fact_cls: type
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    name: str = ""
    scope: tuple[str, ...] = ()

    def __post_init__(self):
        # Normalize the mutable/sequence fields so the frozen dataclass is
        # robust against direct construction with a list or an external dict,
        # and so ``scoped``'s ``(label,) + item.scope`` always sees a tuple.
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "kwargs", dict(self.kwargs))
        object.__setattr__(self, "scope", tuple(self.scope))


@dataclass(frozen=True)
class ActionRequest:
    """A barrier that runs an action now and sends the outcome back.

    Attributes:
        action_cls: The action class exposing ``async def process(host, ...)``.
        args: Positional arguments forwarded to ``process``.
        kwargs: Keyword arguments forwarded to ``process``.
        name: Human-readable label for the report tree.
        scope: Report path set by :func:`scoped`; never reaches a host.
    """

    # The driver executes ``await action_cls.process(host, *args, **kwargs)``.
    # ``Shell.process`` returns a ``Result`` (``exit_status``, ``stdout``,
    # ``stderr``); a non-zero exit is **data**, not an exception, matching the
    # action contract.  Transport and privilege failures raise (design doc
    # section 4.2).
    #
    # ``Act`` is the reason the ``ActionInDeployError`` guarantee needs
    # restating rather than removing: a barrier is executed by the driver,
    # outside the pull, so the guard must be held **during pulls only**.  A
    # ``gather_action(...)`` call written inside the body still raises
    # immediately, while the driver's own ``action_cls.process(...)`` call is
    # unaffected.


    action_cls: type
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    name: str = ""
    scope: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "kwargs", dict(self.kwargs))
        object.__setattr__(self, "scope", tuple(self.scope))


@dataclass(frozen=True)
class CheckpointRequest:
    """A barrier that closes the wave and sends back its ``ApplyReport``.

    Attributes:
        name: Human-readable label for the report tree.
        scope: Report path used to slice the wave's ``ApplyReport``.
    """

    # A checkpoint touches no host.  It participates in the rendezvous (so the
    # wave closes), then sends back an ``ApplyReport`` containing the log entries
    # of the wave that just ended, filtered to entries whose ``scope`` starts
    # with the checkpoint's scope.  The ``yield`` expression *is* an
    # ``ApplyReport`` -- the same type ``run()`` returns -- so ``changed_hosts``
    # works unchanged (design doc section 4.3).
    #
    # A checkpoint must immediately follow the stage it reports on: two
    # checkpoints in a row means the second closes a wave with no requests and
    # reads an empty report, so its notifications are silently lost.



    name: str = ""
    scope: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "scope", tuple(self.scope))


@dataclass(frozen=True)
class ControlRequest:
    """A barrier that runs async control-side IO and sends the result back.

    Attributes:
        func: An async callable executed on the controller.
        args: Positional arguments forwarded to ``func``.
        kwargs: Keyword arguments forwarded to ``func``.
        name: Human-readable label for the report tree.
        scope: Report path set by :func:`scoped`; never reaches a host.
    """

    # The driver executes ``await func(*args, **kwargs)`` on the **controller**,
    # not on a host.  It exists for control-side IO whose input is per-host state
    # -- a variable set entry, a fact, a report -- and which therefore cannot be
    # hoisted out of the per-host generator.  IO that is identical for the whole
    # inventory belongs in an ``async def`` factory instead (design doc section
    # 4.4).
    #
    # A ``Control`` barrier is executed once per **host** parked on it, so N
    # hosts means N calls -- which is exactly why an inventory-wide fetch belongs
    # in the factory instead.


    func: Callable[..., Awaitable[Any]]
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    name: str = ""
    scope: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "kwargs", dict(self.kwargs))
        object.__setattr__(self, "scope", tuple(self.scope))


# The four barrier types as one alias.  A PEP 604 union is valid in
# ``isinstance()``, so the wave loop classifies an item with one check
# (design doc section 3.1).
Barrier = FactRequest | ActionRequest | CheckpointRequest | ControlRequest

# The widened deploy stream: an ``OperationRequest`` or any barrier.
StageItem = OperationRequest | Barrier

# A deploy is a per-host generator yielding ``StageItem``.  An ``async def``
# factory returns exactly this type once awaited (design doc section 9).
Deploy = Callable[[Host], Generator[StageItem, Any, Any]]


def fact(
    fact_cls: type,
    *args: Any,
    name: str = "",
    scope: tuple[str, ...] = (),
    **kwargs: Any,
) -> FactRequest:
    """Build a :class:`FactRequest` barrier.

    ``yield fact(FileExists, path="/etc/ssl/cert.pem")`` parks the generator
    until the driver reads the fact on that host and sends the value back.
    ``name`` labels the barrier in the report tree; ``scope`` is set by
    :func:`scoped`, never by this constructor.

    Every call is charged to the deploy body's built-versus-yielded counter,
    exactly as :func:`~reemote.core.op` charges requests, so a barrier built
    here and then never yielded is caught when the generator runs out.
    """
    request = FactRequest(
        fact_cls=fact_cls, args=args, kwargs=kwargs, name=name, scope=scope
    )
    # Charged only after construction succeeds: a barrier the body failed to
    # build never entered the sequence either, so the two sides stay balanced.
    _charge_built()
    return request


def act(
    action_cls: type,
    *args: Any,
    name: str = "",
    scope: tuple[str, ...] = (),
    **kwargs: Any,
) -> ActionRequest:
    """Build an :class:`ActionRequest` barrier.

    ``yield act(Shell, command="/usr/sbin/apache2 -v", name="apache -v")``
    parks the generator until the driver runs the action on that host and
    sends the ``Result`` back.  A non-zero exit is data, not an exception;
    transport and privilege failures raise.

    Every call is charged to the deploy body's built-versus-yielded counter,
    exactly as :func:`~reemote.core.op` charges requests.
    """
    request = ActionRequest(
        action_cls=action_cls, args=args, kwargs=kwargs, name=name, scope=scope
    )
    _charge_built()
    return request


def checkpoint(
    name: str = "",
    *,
    scope: tuple[str, ...] = (),
) -> CheckpointRequest:
    """Build a :class:`CheckpointRequest` barrier.

    ``wave_report = yield checkpoint("configure done")`` closes the current
    wave and sends back its ``ApplyReport``, sliced to ``scope``, so
    ``changed_hosts(wave_report, names)`` reads only the entries this role
    emitted.  A checkpoint touches no host.

    ``scope`` is keyword-only because a checkpoint carries no payload to
    forward -- its only parameters are the two metadata fields every barrier
    shares.

    Every call is charged to the deploy body's built-versus-yielded counter,
    exactly as :func:`~reemote.core.op` charges requests.
    """
    request = CheckpointRequest(name=name, scope=scope)
    _charge_built()
    return request


def control(
    func: Callable[..., Awaitable[Any]],
    *args: Any,
    name: str = "",
    scope: tuple[str, ...] = (),
    **kwargs: Any,
) -> ControlRequest:
    """Build a :class:`ControlRequest` barrier.

    ``key_text = yield control(files.fetch_url, url)`` parks the generator
    until the driver awaits ``func(*args, **kwargs)`` on the controller and
    sends the return value back.  Use it for control-side IO whose input is
    per-host state; IO that is identical for the whole inventory belongs in an
    ``async def`` factory instead.

    Every call is charged to the deploy body's built-versus-yielded counter,
    exactly as :func:`~reemote.core.op` charges requests.
    """
    request = ControlRequest(
        func=func, args=args, kwargs=kwargs, name=name, scope=scope
    )
    _charge_built()
    return request


def scoped(label: str, deploy: Deploy) -> Deploy:
    """Tag every item a deploy yields with a report path (design doc section 3.3).

    ``yield from scoped("apache", apache(v))(host)`` stamps ``("apache",)``
    onto the front of every item's ``scope`` tuple, so the report tree and
    :func:`checkpoint` slicing can tell which role emitted an item -- without
    rewriting ``OperationRequest.name``, which ``changed_hosts`` matches on.

    The wrapper forwards ``send()`` values to the inner generator and
    propagates its return value, so barriers inside a scoped deploy receive
    their results and a parent can capture the child's outputs with
    ``yield from``.  The design's ``for`` loop sketch does neither -- a ``for``
    loop calls ``next()``, discarding the send value the driver handed the
    wrapper, and swallows ``StopIteration.value`` -- so this implementation
    uses an explicit send loop instead.

    The transform is pure: it keeps no state between calls and reads nothing
    but each item's ``scope``.
    """

    def wrapped(host: Host) -> Generator[StageItem, Any, Any]:
        gen = deploy(host)
        sent: Any = None
        while True:
            try:
                item = gen.send(sent)
            except StopIteration as stop:
                return stop.value
            sent = yield replace(item, scope=(label,) + item.scope)

    return wrapped


__all__ = [
    "ActionRequest",
    "Barrier",
    "CheckpointRequest",
    "ControlRequest",
    "Deploy",
    "FactRequest",
    "RoleRecursionError",
    "StageItem",
    "act",
    "checkpoint",
    "control",
    "fact",
    "scoped",
]
