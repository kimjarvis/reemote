"""Core data structures for the generator-based two-phase execution model.

See the design document sections 4.1 (OperationRequest), 4.3 (OpContext),
4.4 (FactCache) and 4.6 (HostRuntime). None of these objects are aware of
the phase they are used in; phase awareness lives entirely in the drivers.
"""

from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass, field, replace
from typing import Any

from .commands import Command
from .hosts import Host


@dataclass(frozen=True)
class OperationRequest:
    """A plain, frozen, serializable descriptor of "an operation to perform".

    ``frozen=True`` gives value equality, which the lockstep scheduler uses
    to pair "the Nth operation for host A" with "the Nth operation for
    host B". The dict ``kwargs`` field is unhashable, so ``__hash__`` is
    defined explicitly over a hashable projection of the fields.

    ``sudo`` and ``su`` are *meta-parameters*: like ``name`` they describe how
    the driver should run the operation rather than being forwarded to
    ``func``. When true, the driver elevates each command the operation yields
    (the real transport wraps it with ``sudo`` and/or ``su``; the test
    framework's simulated seam ignores them, since privilege has no in-memory
    effect). Note that both are only boolean requests to elevate: *how* to
    elevate -- the run-as users and the passwords -- comes from the host's
    inventory ``privilege`` section, never from the operation, so a deploy
    script cannot choose its own privilege parameters. When both are set the
    escalations combine in a fixed order, ``sudo`` then ``su`` (sudo is the
    outer escalation, running ``su`` inside it).
    """

    func: Callable  # operation function, e.g. dnf.packages
    args: tuple[Any, ...] = ()  # positional args passed to func
    kwargs: dict = field(default_factory=dict)  # keyword args passed to func
    name: str = ""  # human-readable label for plan output
    sudo: bool = False  # meta-parameter: elevate this operation's commands
    su: bool = False  # meta-parameter: run this operation's commands via su

    def __post_init__(self):
        # A dict inside a frozen dataclass is otherwise mutable from outside.
        # The copy is shallow: callers should prefer immutable containers for
        # nested values (e.g. packages=("nginx",)).
        object.__setattr__(self, "kwargs", dict(self.kwargs))

    def __hash__(self):
        return hash(
            (
                self.func,
                self.args,
                tuple(sorted(self.kwargs.items())),
                self.name,
                self.sudo,
                self.su,
            )
        )


@dataclass
class OpContext:
    """Mutable context threaded through one operation invocation.

    This is the mechanism by which an operation communicates ``changed``
    and ``reason`` back to the driver (design doc section 6.3, approach C).
    """

    host: Host
    facts: "FactCache"
    changed: bool = False
    reason: str = ""


class FactCache:
    """Per-host, per-phase fact cache.

    Also recreated at each lockstep step in the apply phase, which is what
    gives generator-body fact reads sequential "act, then read" semantics.
    ``get`` is a coroutine because a fact's ``process`` gathers its value
    asynchronously (over asyncssh in the real implementation).
    """

    def __init__(self, host: Host, phase: str):
        self.host = host
        self.phase = phase  # "prepare" | "apply"
        self._data: dict = {}

    async def get(self, fact_cls, *args, **kwargs):
        key = (fact_cls, args, tuple(sorted(kwargs.items())))
        if key not in self._data:
            self._data[key] = await fact_cls.process(self.host, *args, **kwargs)
        return self._data[key]


@dataclass
class HostRuntime:
    """Per-host state maintained by the apply-phase driver."""

    host: Host
    generator: Generator  # deploy(host) generator
    fact_cache: FactCache
    current_request: OperationRequest | None = None
    current_ctx: OpContext | None = None
    done: bool = False


def op(
    func: Callable, *, name: str = "", sudo: bool = False, su: bool = False, **kwargs
) -> OperationRequest:
    """Convenience helper for building OperationRequests in deploy scripts.

    ``name``, ``sudo`` and ``su`` are meta-parameters consumed by the driver,
    so they are captured here and never forwarded to ``func``; everything else
    in ``kwargs`` is passed through to the operation as keyword arguments.
    """
    return OperationRequest(
        func=func, kwargs=kwargs, name=name, sudo=sudo, su=su
    )


def sub_deploy(
    sub: Callable[[Host], Iterable[OperationRequest]],
    *,
    name_prefix: str = "",
    sudo: bool = False,
    su: bool = False,
) -> Callable[[Host], Generator[OperationRequest]]:
    """Wrap a sub-deploy, applying default meta-parameters to its requests.

    The returned callable has the deploy-script shape -- ``(host)`` ->
    generator of ``OperationRequest`` -- so a wrapped sub-deploy nests with
    ``yield from``, composes with ``compose`` and is accepted anywhere a
    deploy function is (e.g. ``run``).

    Meta-parameters are inherited as defaults and the inner request wins:
    ``sudo=True``/``su=True`` here only elevate requests that did not ask for
    that escalation themselves, and ``name_prefix`` prepends ``"<prefix>: "``
    to each plan name (an unnamed request falls back to its operation
    function's name, matching the formatters). Because ``sudo`` and ``su`` are
    plain bools, an inner request that deliberately set one to ``False`` is
    indistinguishable from one that left it unset -- a wrapper with the flag
    set elevates both.

    The wrapper is a pure stream transform over the frozen dataclass
    (``dataclasses.replace`` produces the modified copies): it keeps no
    state between calls and reads nothing but each request, so the prepare
    and apply phases flatten it identically and the determinism guard holds.
    """

    def wrapper(host: Host) -> Generator[OperationRequest]:
        for request in sub(host):
            changes: dict[str, Any] = {}
            if name_prefix:
                base = request.name or request.func.__name__
                changes["name"] = f"{name_prefix}: {base}"
            if sudo and not request.sudo:
                changes["sudo"] = True
            if su and not request.su:
                changes["su"] = True
            yield replace(request, **changes) if changes else request

    return wrapper


def _effective_key(request: OperationRequest) -> tuple:
    """Hashable identity of what a request *executes*, excluding its name.

    ``name`` labels a request in plans and logs but never reaches the host
    (``command_shell`` renders the descriptor plus ``sudo``/``su`` only), so
    two requests with the same key run identically. ``OperationRequest``
    equality itself stays maximal -- the determinism guard pairs full values
    -- while ``compose``'s deduplication uses this projection.
    """
    return (
        request.func,
        request.args,
        tuple(sorted(request.kwargs.items())),
        request.sudo,
        request.su,
    )


def compose(
    *subs: Callable[[Host], Iterable[OperationRequest]],
    dedupe: bool = False,
) -> Callable[[Host], Generator[OperationRequest]]:
    """Concatenate sub-deploys into one deploy-shaped callable, in order.

    Each ``sub`` is any callable taking a ``Host`` and producing
    ``OperationRequest`` objects (a deploy generator function, or one wrapped
    by ``sub_deploy``); their requests are yielded in argument order, so
    composition doubles as an explicit cross-role ordering statement (e.g.
    repositories before the packages they provide).

    With ``dedupe=True``, requests collapse by *effective identity* --
    ``func``, ``args``, ``kwargs``, ``sudo`` and ``su``, but not ``name`` (see
    ``_effective_key``) -- to their first occurrence, which keeps its name.
    Independently written roles may therefore each declare the same
    prerequisite under a different label or a different ``sub_deploy`` name
    prefix and still collapse to one plan entry; ``sudo`` and ``su`` still
    separate requests, since they change what executes. ``OperationRequest``
    equality remains maximal (name included), because the determinism guard
    pairs full values.

    The ``seen`` set is created inside the returned deploy, per invocation:
    every ``deploy(host)`` call (one per host per phase) dedupes from
    scratch, keeping the transform pure so both phases produce the identical
    collapsed sequence and the determinism guard holds.
    """

    def deploy(host: Host) -> Generator[OperationRequest]:
        seen: set[tuple] = set()
        for sub in subs:
            for request in sub(host):
                if dedupe:
                    key = _effective_key(request)
                    if key in seen:
                        continue
                    seen.add(key)
                yield request

    return deploy


__all__ = [
    "Command",
    "FactCache",
    "HostRuntime",
    "OpContext",
    "OperationRequest",
    "compose",
    "op",
    "sub_deploy",
]
