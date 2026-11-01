"""Core data structures for the generator-based two-phase execution model.

See the design document sections 4.1 (OperationRequest), 4.3 (OpContext),
4.4 (FactCache) and 4.6 (HostRuntime). None of these objects are aware of
the phase they are used in; phase awareness lives entirely in the drivers.
"""

from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from .commands import Command
from .hosts import Host

# A ``when`` guard predicate: reads the host (static inventory data -- name,
# group, tags, ...) and answers "is this host eligible for the request?".
WhenPredicate = Callable[[Host], bool]


def _coerce_when(
    value: "WhenPredicate | tuple[WhenPredicate, ...] | None",
) -> tuple[WhenPredicate, ...]:
    """Normalize the accepted ``when`` spellings to a tuple of predicates.

    ``None`` (the default) becomes the empty tuple -- no guard, every host is
    eligible -- a single callable is wrapped, and a tuple passes through.
    Storing a *tuple* rather than one composed callable is what keeps guard
    composition value-stable: ``sub_deploy`` concatenates tuples instead of
    building a fresh closure per request, so the prepare and apply phases
    produce equal ``when`` values (predicates compare by identity, like
    ``func``) and the determinism guard holds.
    """
    if value is None:
        return ()
    if callable(value):
        return (value,)
    return tuple(value)


class Privilege(Enum):
    """Tri-state privilege-escalation request for the ``sudo``/``su`` meta-parameters.

    A plain ``bool`` conflates two different intentions: "run this operation
    unprivileged" and "no opinion, let a wrapper decide". Because they are the
    same ``False``, a ``sub_deploy(sudo=True)`` policy cannot tell a request
    that deliberately opted out from one that was simply never flagged, and
    elevates both. ``Privilege`` separates the three states so forced elevation
    can honour an explicit opt-out:

    - ``ON`` -- elevate this operation's commands.
    - ``OFF`` -- deliberately run unprivileged; a forced-elevation wrapper
      (``sub_deploy(sudo=Privilege.ON)``) respects it. This is the opt-out a
      plain bool cannot express.
    - ``UNSET`` -- no opinion. A wrapper may fill it in; with no wrapper left it
      resolves to "do not elevate" at the execution boundary, so an unflagged
      top-level request stays unprivileged exactly as ``sudo=False`` did before.

    The execution layer (``drivers.command_shell``, ``Host.run_shell_privileged``,
    ``LogEntry``) deals in concrete bools, so the driver collapses a request's
    tri-state with ``elevates()`` before sending: only ``ON`` elevates.
    """

    ON = "on"
    OFF = "off"
    UNSET = "unset"

    def elevates(self) -> bool:
        """The concrete elevation decision: ``True`` only for ``ON``.

        ``OFF`` and ``UNSET`` both collapse to ``False`` -- by the time a
        request reaches the driver every wrapper has run, so a still-``UNSET``
        flag means nothing claimed it and it must not elevate.
        """
        return self is Privilege.ON

    @classmethod
    def coerce(cls, value: "Privilege | bool | None") -> "Privilege":
        """Normalize the accepted spellings to a ``Privilege`` member.

        Accepts a ``Privilege`` (returned unchanged), ``None`` (-> ``UNSET``)
        and a plain ``bool`` for ergonomic call sites -- ``True`` -> ``ON``,
        ``False`` -> ``OFF`` -- so existing ``sudo=True``/``sudo=False`` keep
        working while the omitted default stays ``UNSET``.
        """
        if isinstance(value, Privilege):
            return value
        if value is None:
            return cls.UNSET
        return cls.ON if value else cls.OFF


def _inherit(policy: Privilege, inner: Privilege) -> Privilege:
    """Apply a wrapper's escalation ``policy`` to an inner request's ``inner`` flag.

    The inner request wins whenever it has an opinion (``ON`` or ``OFF``); the
    wrapper only fills the ``UNSET`` ones. This is what makes
    ``sub_deploy(sudo=Privilege.ON)`` a *forced-elevation-with-opt-out* policy:
    it elevates every request that did not deliberately set ``sudo=False``.
    """
    return inner if inner is not Privilege.UNSET else policy


@dataclass(frozen=True)
class OperationRequest:
    """A plain, frozen, serializable descriptor of "an operation to perform".

    ``frozen=True`` gives value equality, which the lockstep scheduler uses
    to pair "the Nth operation for host A" with "the Nth operation for
    host B". The dict ``kwargs`` field is unhashable, so ``__hash__`` is
    defined explicitly over a hashable projection of the fields.

    ``sudo`` and ``su`` are *meta-parameters*: like ``name`` they describe how
    the driver should run the operation rather than being forwarded to
    ``func``. Each is a :class:`Privilege` tri-state, not a plain bool, so a
    request can distinguish "elevate" (``ON``), "deliberately run unprivileged"
    (``OFF``) and "no opinion" (``UNSET``, the default). Only ``ON`` elevates:
    the driver collapses the tri-state with ``Privilege.elevates`` before
    sending, wrapping the command with ``sudo`` and/or ``su`` on the real
    transport (the test framework's simulated seam ignores them, since
    privilege has no in-memory effect). The tri-state is what lets a
    ``sub_deploy`` policy force elevation while honouring an explicit opt-out
    -- see ``sub_deploy``. Note that these are only requests to elevate: *how*
    to elevate -- the run-as users and the passwords -- comes from the host's
    inventory ``privilege`` section, never from the operation, so a deploy
    script cannot choose its own privilege parameters. When both are ``ON`` the
    escalations combine in a fixed order, ``sudo`` then ``su`` (sudo is the
    outer escalation, running ``su`` inside it).

    ``when`` is the eligibility meta-parameter: a guard predicate (or tuple
    of predicates) over *static* host data, stored normalized as a tuple
    (empty = always eligible). The driver evaluates the predicates at the
    execution boundary -- a request whose guard fails on a host is skipped
    there, but stays in the host's request sequence, so every host shares the
    same schedule and lockstep pairing is untouched. Unlike ``sudo``/``su``
    (configuration values a wrapper fills in), guards compose by conjunction:
    a ``sub_deploy`` guard and an inner guard must *both* pass. Predicates
    must read only static inventory data (``host.name``, ``host.group``,
    ``host.tags``, ...) -- never mutable facts -- so both phases reach the
    same skip decision, and they must be stable function objects (module- or
    composition-level definitions, like ``func`` itself): an inline
    ``lambda`` in a deploy body is a fresh object on each phase invocation,
    which the determinism guard correctly reports as divergence.
    """

    func: Callable  # operation function, e.g. dnf.packages
    args: tuple[Any, ...] = ()  # positional args passed to func
    kwargs: dict = field(default_factory=dict)  # keyword args passed to func
    name: str = ""  # human-readable label for plan output
    sudo: Privilege = Privilege.UNSET  # meta-parameter: elevate via sudo
    su: Privilege = Privilege.UNSET  # meta-parameter: elevate via su
    when: WhenPredicate | tuple[WhenPredicate, ...] | None = None  # eligibility guard(s)

    def __post_init__(self):
        # A dict inside a frozen dataclass is otherwise mutable from outside.
        # The copy is shallow: callers should prefer immutable containers for
        # nested values (e.g. packages=("nginx",)).
        object.__setattr__(self, "kwargs", dict(self.kwargs))
        # Normalize the escalation flags so the ergonomic bool/None spellings
        # (``sudo=True``) and the ``Privilege`` members coexist: every stored
        # value is a ``Privilege``, so ``elevates()`` is always available.
        object.__setattr__(self, "sudo", Privilege.coerce(self.sudo))
        object.__setattr__(self, "su", Privilege.coerce(self.su))
        # Normalize the guard(s) to a tuple so the stored value is hashable,
        # value-comparable and conjunction-composable (see ``_coerce_when``).
        object.__setattr__(self, "when", _coerce_when(self.when))

    def __hash__(self):
        return hash(
            (
                self.func,
                self.args,
                tuple(sorted(self.kwargs.items())),
                self.name,
                self.sudo,
                self.su,
                self.when,
            )
        )

    def when_failure(self, host: Host) -> WhenPredicate | None:
        """The first guard predicate ``host`` fails, or ``None`` when eligible.

        Short-circuits in tuple order (outer ``sub_deploy`` guards first), so
        the returned predicate names the *outermost* reason a host is skipped
        -- which is what the plan and run log report. An empty ``when`` tuple
        means no guard: every host is eligible.
        """
        for predicate in self.when:
            if not predicate(host):
                return predicate
        return None


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


class OperationError(RuntimeError):
    """Raised when an operation's prerequisites are not met on a host.

    An operation may require a capability the host does not have -- e.g.
    ``server.timezone`` requires ``timedatectl`` and is supported on systemd
    systems only. Rather than silently skipping the host or yielding a
    command that cannot run, the operation raises this while materializing,
    which in a deploy run happens in the prepare phase -- before anything is
    sent -- so the run aborts fail-fast like ``CommandError``, with a message
    naming the host and the missing prerequisite.
    """


def op(
    func: Callable,
    *,
    name: str = "",
    sudo: Privilege | bool = Privilege.UNSET,
    su: Privilege | bool = Privilege.UNSET,
    when: WhenPredicate | tuple[WhenPredicate, ...] | None = None,
    **kwargs,
) -> OperationRequest:
    """Convenience helper for building OperationRequests in deploy scripts.

    ``name``, ``sudo``, ``su`` and ``when`` are meta-parameters consumed by
    the driver, so they are captured here and never forwarded to ``func``;
    everything else in ``kwargs`` is passed through to the operation as
    keyword arguments.

    ``sudo``/``su`` accept a :class:`Privilege` or a plain bool (``True`` ->
    ``ON``, ``False`` -> ``OFF``) and default to ``UNSET`` -- "no opinion", so
    a ``sub_deploy`` policy can still fill them in. ``OperationRequest``
    normalizes the value, so the stored flag is always a ``Privilege``.

    ``when`` is an eligibility guard: a predicate over static host data
    (``lambda``-free at this call site -- pass a stable, module-level function
    so both phases yield equal requests), a tuple of predicates (all must
    pass), or ``None`` (the default, always eligible). A guarded request stays
    in every host's sequence; the driver skips it on hosts that fail the
    guard. ``OperationRequest`` normalizes the value to a tuple.
    """
    return OperationRequest(
        func=func,
        kwargs=kwargs,
        name=name,
        sudo=Privilege.coerce(sudo),
        su=Privilege.coerce(su),
        when=when,
    )


def sub_deploy(
    sub: Callable[[Host], Iterable[OperationRequest]],
    *,
    name_prefix: str = "",
    sudo: Privilege | bool = Privilege.UNSET,
    su: Privilege | bool = Privilege.UNSET,
    when: WhenPredicate | tuple[WhenPredicate, ...] | None = None,
) -> Callable[[Host], Generator[OperationRequest]]:
    """Wrap a sub-deploy, applying default meta-parameters to its requests.

    The returned callable has the deploy-script shape -- ``(host)`` ->
    generator of ``OperationRequest`` -- so a wrapped sub-deploy nests with
    ``yield from``, composes with ``compose`` and is accepted anywhere a
    deploy function is (e.g. ``run``).

    ``sudo``/``su`` are an escalation *policy* for the wrapped requests, given
    as a :class:`Privilege` (or a bool: ``True`` -> ``ON``). The inner request
    wins whenever it has an opinion, and the policy only fills the ``UNSET``
    ones (see ``_inherit``):

    - ``sub_deploy(sudo=Privilege.ON)`` (equivalently ``sudo=True``) forces
      elevation on every request that did not *deliberately* opt out -- an
      inner ``sudo=False``/``Privilege.OFF`` stays unprivileged, while an
      unflagged (``UNSET``) one is elevated. This forced-elevation-with-opt-out
      is exactly what a plain bool could not express.
    - ``sub_deploy(sudo=Privilege.OFF)`` forces the ``UNSET`` requests
      unprivileged, leaving an inner ``ON`` untouched.
    - the default ``Privilege.UNSET`` is no policy at all: every request passes
      through unchanged.

    ``when`` is a role-level eligibility guard -- the analogue of Ansible's
    block-level ``when``. Unlike the escalation policy it composes by
    *conjunction*, not inner-wins: the wrapper's predicates are prepended to
    each request's own ``when`` tuple, so both must pass and a wrapper can
    only ever *restrict* eligibility, never widen it (a guard the inside
    could override would be no guard at all). The default ``None`` adds no
    predicate. Concatenating tuples -- rather than building a composed
    closure per request -- keeps the transform value-stable across phases,
    so the determinism guard holds.

    ``name_prefix`` prepends ``"<prefix>: "`` to each plan name (an unnamed
    request falls back to its operation function's name, matching the
    formatters).

    The wrapper is a pure stream transform over the frozen dataclass
    (``dataclasses.replace`` produces the modified copies): it keeps no
    state between calls and reads nothing but each request, so the prepare
    and apply phases flatten it identically and the determinism guard holds.
    """
    sudo_policy = Privilege.coerce(sudo)
    su_policy = Privilege.coerce(su)
    when_policy = _coerce_when(when)

    def wrapper(host: Host) -> Generator[OperationRequest]:
        for request in sub(host):
            changes: dict[str, Any] = {}
            if name_prefix:
                base = request.name or request.func.__name__
                changes["name"] = f"{name_prefix}: {base}"
            inherited_sudo = _inherit(sudo_policy, request.sudo)
            if inherited_sudo is not request.sudo:
                changes["sudo"] = inherited_sudo
            inherited_su = _inherit(su_policy, request.su)
            if inherited_su is not request.su:
                changes["su"] = inherited_su
            if when_policy:
                # Conjunction by concatenation: outer guards first, so a
                # skip reports the outermost failing predicate.
                changes["when"] = when_policy + request.when
            yield replace(request, **changes) if changes else request

    return wrapper


def _effective_key(request: OperationRequest) -> tuple:
    """Hashable identity of what a request *executes*, excluding its name.

    ``name`` labels a request in plans and logs but never reaches the host
    (``command_shell`` renders the descriptor plus the resolved ``sudo``/``su``
    only), so two requests with the same key run identically. The escalation
    flags contribute their *resolved* decision (``Privilege.elevates``), not the
    raw tri-state: ``ON`` separates a request from the rest, while ``OFF`` and
    ``UNSET`` collapse together because both execute unprivileged. The
    ``when`` guard tuple participates as-is: it changes *whether* a request
    executes, so two otherwise-identical requests with different guards are
    not duplicates.
    ``OperationRequest`` equality itself stays maximal -- the determinism guard
    pairs full values, tri-state included -- while ``compose``'s deduplication
    uses this projection.
    """
    return (
        request.func,
        request.args,
        tuple(sorted(request.kwargs.items())),
        request.sudo.elevates(),
        request.su.elevates(),
        request.when,
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
    ``func``, ``args``, ``kwargs``, ``sudo``, ``su`` and ``when``, but not
    ``name`` (see ``_effective_key``) -- to their first occurrence, which keeps
    its name.
    Independently written roles may therefore each declare the same
    prerequisite under a different label or a different ``sub_deploy`` name
    prefix and still collapse to one plan entry; ``sudo``/``su`` still
    separate requests, since they change what executes, and so does ``when``,
    since it changes whether it executes. ``OperationRequest``
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
    "OperationError",
    "OperationRequest",
    "Privilege",
    "WhenPredicate",
    "compose",
    "op",
    "sub_deploy",
]
