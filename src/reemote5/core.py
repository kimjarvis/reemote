"""Core data structures for the generator-based two-phase execution model.

See the design document sections 4.1 (OperationRequest), 4.3 (OpContext),
4.4 (FactCache) and 4.6 (HostRuntime). None of these objects are aware of
the phase they are used in; phase awareness lives entirely in the drivers.
"""

from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import Any

from .commands import Command
from .hosts import SimulatedHost


@dataclass(frozen=True)
class OperationRequest:
    """A plain, frozen, serializable descriptor of "an operation to perform".

    ``frozen=True`` gives value equality, which the lockstep scheduler uses
    to pair "the Nth operation for host A" with "the Nth operation for
    host B". The dict ``kwargs`` field is unhashable, so ``__hash__`` is
    defined explicitly over a hashable projection of the fields.
    """

    func: Callable  # operation function, e.g. apt.packages
    args: tuple[Any, ...] = ()  # positional args passed to func
    kwargs: dict = field(default_factory=dict)  # keyword args passed to func
    name: str = ""  # human-readable label for plan output

    def __post_init__(self):
        # A dict inside a frozen dataclass is otherwise mutable from outside.
        # The copy is shallow: callers should prefer immutable containers for
        # nested values (e.g. packages=("nginx",)).
        object.__setattr__(self, "kwargs", dict(self.kwargs))

    def __hash__(self):
        return hash(
            (self.func, self.args, tuple(sorted(self.kwargs.items())), self.name)
        )


@dataclass
class OpContext:
    """Mutable context threaded through one operation invocation.

    This is the mechanism by which an operation communicates ``changed``
    and ``reason`` back to the driver (design doc section 6.3, approach C).
    """

    host: Any
    facts: "FactCache"
    changed: bool = False
    reason: str = ""


class FactCache:
    """Per-host, per-phase fact cache.

    Also recreated at each lockstep step in the apply phase, which is what
    gives generator-body fact reads sequential "act, then read" semantics.
    """

    def __init__(self, host: Any, phase: str):
        self.host = host
        self.phase = phase  # "prepare" | "apply"
        self._data: dict = {}

    def get(self, fact_cls, *args, **kwargs):
        key = (fact_cls, args, tuple(sorted(kwargs.items())))
        if key not in self._data:
            self._data[key] = fact_cls.process(self.host, *args, **kwargs)
        return self._data[key]


@dataclass
class HostRuntime:
    """Per-host state maintained by the apply-phase driver."""

    host: SimulatedHost
    generator: Generator  # deploy(host) generator
    fact_cache: FactCache
    current_request: OperationRequest | None = None
    current_ctx: OpContext | None = None
    done: bool = False


def op(func: Callable, *, name: str = "", **kwargs) -> OperationRequest:
    """Convenience helper for building OperationRequests in deploy scripts."""
    return OperationRequest(func=func, kwargs=kwargs, name=name)


__all__ = [
    "Command",
    "FactCache",
    "HostRuntime",
    "OpContext",
    "OperationRequest",
    "op",
]
