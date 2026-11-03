# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Core data structures for the generator-based two-phase execution model.

See the design document sections 4.1 (OperationRequest), 4.3 (OpContext),
4.4 (FactCache) and 4.6 (HostRuntime). None of these objects are aware of
the phase they are used in; phase awareness lives entirely in the drivers.
"""

from collections.abc import Callable, Generator, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
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
    """Tri-state privilege-escalation request for the ``sudo``/``su``/``doas``
    meta-parameters.

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


class UnhashableArgumentError(TypeError):
    """Raised when an ``OperationRequest`` carries an unhashable argument value.

    Pairing hosts in lockstep and the determinism guard both compare requests by
    *value*; hashing comes in separately, where ``compose(dedupe=True)`` builds
    a ``set`` of ``_effective_key`` tuples -- and every argument value is part of
    that key. An unhashable value (a ``list``, a ``dict``, a ``set``, or one of
    those nested inside a tuple) therefore breaks deduplication with a bare
    ``TypeError`` raised from inside a set insertion, far from the deploy line
    that caused it. So the request validates its own arguments at construction
    time, while the offending call site is still on the traceback.

    Subclasses ``TypeError`` -- what it replaces -- so existing handlers keep
    working.
    """


def _ensure_hashable(location: str, value: Any) -> None:
    """Reject an unhashable request argument, naming where it came from.

    ``location`` is the call-site label -- ``args[0]`` or ``kwargs['packages']``
    -- so the message points straight at the deploy line. Hashing the value *is*
    the check: unlike a top-level type test it also catches a mutable container
    nested inside an immutable one, such as ``packages=(["nginx"],)``, which
    ``hash`` rejects just the same.
    """
    try:
        hash(value)
    except TypeError as exc:
        raise UnhashableArgumentError(
            f"{location} is unhashable ({exc}); OperationRequest arguments must "
            "stay hashable because compose(dedupe=True) hashes every one of "
            'them -- use immutable containers, e.g. packages=("nginx",) not '
            'packages=["nginx"]'
        ) from exc


@dataclass(frozen=True)
class OperationRequest:
    """A plain, frozen, serializable descriptor of "an operation to perform".

    ``frozen=True`` gives value equality, which is what the lockstep scheduler
    compares to pair "the Nth operation for host A" with "the Nth operation for
    host B" -- and to check an apply-phase request against the prepare-phase
    plan. Hashing is a second, narrower need: the dict ``kwargs`` field is
    unhashable, so ``__hash__`` is defined explicitly over a hashable projection
    of the fields, and it is ``compose(dedupe=True)`` (which puts ``_effective_key``
    tuples in a set) that calls it. Because that key includes every argument
    value, an unhashable one -- ``packages=["nginx"]`` -- is rejected here, in
    ``__post_init__``, with ``UnhashableArgumentError``.

    ``sudo``, ``su`` and ``doas`` are *meta-parameters*: like ``name`` they
    describe how the driver should run the operation rather than being forwarded
    to ``func``. Each is a :class:`Privilege` tri-state, not a plain bool, so a
    request can distinguish "elevate" (``ON``), "deliberately run unprivileged"
    (``OFF``) and "no opinion" (``UNSET``, the default). Only ``ON`` elevates:
    the driver collapses the tri-state with ``Privilege.elevates`` before
    sending, wrapping the command with ``doas``, ``sudo`` and/or ``su`` on the
    real transport (the test framework's simulated seam ignores them, since
    privilege has no in-memory effect). The tri-state is what lets a
    ``sub_deploy`` policy force elevation while honouring an explicit opt-out
    -- see ``sub_deploy``. Note that these are only requests to elevate: *how*
    to elevate -- the run-as users and the passwords -- comes from the host's
    inventory ``privilege`` section, never from the operation, so a deploy
    script cannot choose its own privilege parameters. When several are ``ON``
    the escalations combine in a fixed order, ``doas`` then ``sudo`` then ``su``
    (``doas`` is the outermost escalation and ``su`` the innermost, so ``doas``
    runs ``sudo``, which runs ``su`` inside it).

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
    doas: Privilege = Privilege.UNSET  # meta-parameter: elevate via doas
    when: WhenPredicate | tuple[WhenPredicate, ...] | None = None  # eligibility guard(s)

    def __post_init__(self):
        # A dict inside a frozen dataclass is otherwise mutable from outside.
        # The copy is shallow, so nested values must be immutable themselves --
        # enforced by the hashability check at the end of this method.
        object.__setattr__(self, "kwargs", dict(self.kwargs))
        # ``args`` participates in ``__hash__`` as stored, so a list passed
        # positionally would make the whole request unhashable; normalize it to
        # a tuple, mirroring the ``kwargs`` copy.
        object.__setattr__(self, "args", tuple(self.args))
        # Normalize the escalation flags so the ergonomic bool/None spellings
        # (``sudo=True``) and the ``Privilege`` members coexist: every stored
        # value is a ``Privilege``, so ``elevates()`` is always available.
        object.__setattr__(self, "sudo", Privilege.coerce(self.sudo))
        object.__setattr__(self, "su", Privilege.coerce(self.su))
        object.__setattr__(self, "doas", Privilege.coerce(self.doas))
        # Normalize the guard(s) to a tuple so the stored value is hashable,
        # value-comparable and conjunction-composable (see ``_coerce_when``).
        object.__setattr__(self, "when", _coerce_when(self.when))
        # Fail fast on an unhashable argument: deduplication hashes every value,
        # and a deploy is the only place left where the call site is identifiable
        # (see ``UnhashableArgumentError``).
        for index, value in enumerate(self.args):
            _ensure_hashable(f"args[{index}]", value)
        for key, value in self.kwargs.items():
            _ensure_hashable(f"kwargs[{key!r}]", value)

    def __hash__(self):
        return hash(
            (
                self.func,
                self.args,
                tuple(sorted(self.kwargs.items())),
                self.name,
                self.sudo,
                self.su,
                self.doas,
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


class DeployRequestError(TypeError):
    """Raised when a deploy body breaks the "yield requests" contract.

    A deploy is a plain sync generator whose body hands the driver exactly one
    ``OperationRequest`` per ``yield``. Breaking that shape loses work in
    silence, or fails with an error raised so deep in the driver that it names
    nothing useful, so the driver checks the contract at the two points where a
    deploy body is invoked (``drivers._prepare_host`` and the lockstep pull in
    ``drivers.run_apply``) -- which in a normal run means the prepare phase,
    before anything is sent:

    - the body returned a non-iterable (typically ``None``) because it calls
      ``op()`` without ``yield``, so there is no request stream at all;
    - a yielded item is not an ``OperationRequest`` -- e.g. an operation called
      from the deploy body, which cannot even be invoked without the
      ``OpContext`` only the driver holds;
    - the body built a request with ``op()`` and dropped it instead of yielding
      it. This is the shape nothing else can catch: the sequence stays valid,
      is identically shorter in both phases (so the determinism guard holds)
      and the operation simply never runs. It is caught by the per-invocation
      built-versus-yielded tally in ``_RequestCounter``.

    Subclasses ``TypeError`` -- the bare error these misuses used to raise from
    inside the driver -- so a caller handling ``TypeError`` keeps handling it,
    and the message names the host, the phase and the missing ``yield``.
    """


@dataclass
class _RequestCounter:
    """Per-invocation tally of the requests a deploy body built and yielded.

    ``op()`` charges every request it constructs to the counter installed for
    the deploy invocation in progress, the driver charges every request that
    actually reaches the sequence, and ``compose(..., dedupe=True)`` charges the
    requests it collapses on purpose. A body that builds a request and loses it
    therefore leaves ``unyielded`` positive, which the driver reports as
    ``DeployRequestError`` once the generator is exhausted.

    The counter reaches ``op()`` through a ``ContextVar`` because ``op()`` sits
    in the deploy body, which the driver only ever calls as an opaque callable;
    set-and-reset-around-the-invocation is the same token discipline
    ``actions._deploy_phase`` uses, so the tally stays per-invocation and
    per-task. Both phases install a counter identically, so it is a pure
    function of the body and never disturbs the determinism guard.
    """

    built: int = 0  # op() calls made while this counter was installed
    yielded: int = 0  # requests the body handed the driver
    dropped: int = 0  # requests a compose dedupe collapsed on purpose

    @property
    def unyielded(self) -> int:
        """Requests built and then lost: never yielded, never deduped.

        Clamped at zero. A body may legitimately hand over requests it built
        outside the tracked invocation -- a module-level list of ``op()`` calls
        it then ``yield from`` -- which leaves the tally negative, and a
        negative tally is over-accounting, not a lost request.
        """
        return max(0, self.built - self.yielded - self.dropped)


_REQUEST_COUNTER: ContextVar["_RequestCounter | None"] = ContextVar(
    "reemote5_request_counter", default=None
)


def _current_counter() -> "_RequestCounter | None":
    """The counter installed for the deploy invocation in progress, if any."""
    return _REQUEST_COUNTER.get()


@contextmanager
def _tracked_requests(counter: _RequestCounter) -> Iterator[_RequestCounter]:
    """Install ``counter`` as the tally for the deploy body run inside.

    Requests the body builds with ``op()`` are charged to it for the duration,
    and the ``ContextVar`` is reset even when the body raises, so a failed
    deploy leaves no counter behind for the next invocation.
    """
    token = _REQUEST_COUNTER.set(counter)
    try:
        yield counter
    finally:
        _REQUEST_COUNTER.reset(token)


def _account_request(
    item: Any, counter: "_RequestCounter | None", host_name: str, phase: str
) -> "OperationRequest":
    """Check one item a deploy body produced and charge it to ``counter``.

    The driver calls this on every item the body hands it, in both phases, so
    the type rule is enforced once per yielded request and the yielded count
    stays paired with the ``op()`` builds ``_tracked_requests`` tallies.

    ``counter`` is ``None`` for a runtime built without deploy accounting (a
    caller that hand-assembles a ``HostRuntime``), which keeps the type check
    while leaving the built-versus-yielded tally untracked.
    """
    if not isinstance(item, OperationRequest):
        raise DeployRequestError(
            f"host {host_name}: the {phase}-phase deploy body yielded {item!r}, "
            f"a {type(item).__name__}, not an OperationRequest; yield the "
            "requests op() builds (yield op(dnf.packages, ...)) and never call "
            "an operation from a deploy body, which needs the OpContext only "
            "the driver holds"
        )
    if counter is not None:
        counter.yielded += 1
    return item


def _lost_requests(host_name: str, count: int) -> str:
    """The per-host clause of an unyielded-request report, pluralised."""
    return f"host {host_name} lost {count} built request{'' if count == 1 else 's'}"


def _unyielded_message(details: str, phase: str) -> str:
    """Report requests a deploy body built and lost, naming hosts and phase.

    ``details`` is the per-host part from ``_lost_requests``, already joined for
    an apply-phase step where several hosts lost a request in the same lockstep
    pull.
    """
    return (
        f"{details} -- a {phase}-phase deploy body must yield every request it "
        "builds with op(), because a built-but-unyielded request is silently "
        "dropped: write yield op(...), not op(...)"
    )


@dataclass
class HostRuntime:
    """Per-host state maintained by the apply-phase driver.

    ``deploy_counter`` is the built-versus-yielded tally for this host's
    *apply-phase* generator, installed by ``run_apply`` around each request
    pull. ``_prepare_host`` gives every runtime a fresh one, so a deploy body
    that loses a request is caught in the prepare phase first and again here if
    a caller drives apply on a runtime it built itself.
    """

    host: Host
    generator: Generator  # deploy(host) generator
    fact_cache: FactCache
    current_request: OperationRequest | None = None
    current_ctx: OpContext | None = None
    done: bool = False
    deploy_counter: _RequestCounter = field(default_factory=_RequestCounter)


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
    doas: Privilege | bool = Privilege.UNSET,
    when: WhenPredicate | tuple[WhenPredicate, ...] | None = None,
    **kwargs,
) -> OperationRequest:
    """Convenience helper for building OperationRequests in deploy scripts.

    ``name``, ``sudo``, ``su``, ``doas`` and ``when`` are meta-parameters
    consumed by the driver, so they are captured here and never forwarded to
    ``func``; everything else in ``kwargs`` is passed through to the operation
    as keyword arguments.

    ``sudo``/``su``/``doas`` accept a :class:`Privilege` or a plain bool
    (``True`` -> ``ON``, ``False`` -> ``OFF``) and default to ``UNSET`` -- "no
    opinion", so a ``sub_deploy`` policy can still fill them in.
    ``OperationRequest`` normalizes the value, so the stored flag is always a
    ``Privilege``.

    ``when`` is an eligibility guard: a predicate over static host data
    (``lambda``-free at this call site -- pass a stable, module-level function
    so both phases yield equal requests), a tuple of predicates (all must
    pass), or ``None`` (the default, always eligible). A guarded request stays
    in every host's sequence; the driver skips it on hosts that fail the
    guard. ``OperationRequest`` normalizes the value to a tuple.

    Collection arguments must be immutable: ``packages=("nginx",)``, never
    ``packages=["nginx"]``. ``OperationRequest`` checks each ``args``/``kwargs``
    value is hashable and raises ``UnhashableArgumentError`` on the spot, so a
    list -- or a list nested inside a tuple -- is named at the deploy line
    instead of failing later inside ``compose(dedupe=True)``.

    Every call is charged to the deploy body's built-versus-yielded counter when
    one is installed, which is what lets the driver catch a request that was
    built here and then never yielded (``yield op(...)``, not ``op(...)``) with
    ``DeployRequestError``. Outside a tracked deploy invocation the count is a
    no-op, so building requests anywhere else is unaffected.
    """
    request = OperationRequest(
        func=func,
        kwargs=kwargs,
        name=name,
        sudo=Privilege.coerce(sudo),
        su=Privilege.coerce(su),
        doas=Privilege.coerce(doas),
        when=when,
    )
    # Charged only after construction succeeds: a request the body failed to
    # build never entered the sequence either, so the two sides stay balanced.
    counter = _current_counter()
    if counter is not None:
        counter.built += 1
    return request


def sub_deploy(
    sub: Callable[[Host], Iterable[OperationRequest]],
    *,
    name_prefix: str = "",
    sudo: Privilege | bool = Privilege.UNSET,
    su: Privilege | bool = Privilege.UNSET,
    doas: Privilege | bool = Privilege.UNSET,
    when: WhenPredicate | tuple[WhenPredicate, ...] | None = None,
) -> Callable[[Host], Generator[OperationRequest]]:
    """Wrap a sub-deploy, applying default meta-parameters to its requests.

    The returned callable has the deploy-script shape -- ``(host)`` ->
    generator of ``OperationRequest`` -- so a wrapped sub-deploy nests with
    ``yield from``, composes with ``compose`` and is accepted anywhere a
    deploy function is (e.g. ``run``).

    ``sudo``/``su``/``doas`` are an escalation *policy* for the wrapped
    requests, given as a :class:`Privilege` (or a bool: ``True`` -> ``ON``). The
    inner request wins whenever it has an opinion, and the policy only fills the
    ``UNSET`` ones (see ``_inherit``):

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
    doas_policy = Privilege.coerce(doas)
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
            inherited_doas = _inherit(doas_policy, request.doas)
            if inherited_doas is not request.doas:
                changes["doas"] = inherited_doas
            if when_policy:
                # Conjunction by concatenation: outer guards first, so a
                # skip reports the outermost failing predicate.
                changes["when"] = when_policy + request.when
            yield replace(request, **changes) if changes else request

    return wrapper


def _effective_key(request: OperationRequest) -> tuple:
    """Hashable identity of what a request *executes*, excluding its name.

    ``name`` labels a request in plans and logs but never reaches the host
    (``command_shell`` renders the descriptor plus the resolved
    ``sudo``/``su``/``doas`` only), so two requests with the same key run
    identically. The escalation flags contribute their *resolved* decision
    (``Privilege.elevates``), not the raw tri-state: ``ON`` separates a request
    from the rest, while ``OFF`` and ``UNSET`` collapse together because both
    execute unprivileged. The
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
        request.doas.elevates(),
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
    ``func``, ``args``, ``kwargs``, ``sudo``, ``su``, ``doas`` and ``when``,
    but not ``name`` (see ``_effective_key``) -- to their first occurrence,
    which keeps its name.
    Independently written roles may therefore each declare the same
    prerequisite under a different label or a different ``sub_deploy`` name
    prefix and still collapse to one plan entry; ``sudo``/``su``/``doas`` still
    separate requests, since they change what executes, and so does ``when``,
    since it changes whether it executes. ``OperationRequest``
    equality remains maximal (name included), because the determinism guard
    pairs full values.

    The ``seen`` set is created inside the returned deploy, per invocation:
    every ``deploy(host)`` call (one per host per phase) dedupes from
    scratch, keeping the transform pure so both phases produce the identical
    collapsed sequence and the determinism guard holds.

    A request dropped as a duplicate is charged to the deploy body's
    built-versus-yielded counter as ``dropped``, because it was built on purpose
    and collapsed on purpose -- without it the driver's ``DeployRequestError``
    check would read a dedupe as a lost request.
    """

    def deploy(host: Host) -> Generator[OperationRequest]:
        seen: set[tuple] = set()
        for sub in subs:
            for request in sub(host):
                if dedupe:
                    key = _effective_key(request)
                    if key in seen:
                        counter = _current_counter()
                        if counter is not None:
                            counter.dropped += 1
                        continue
                    seen.add(key)
                yield request

    return deploy


__all__ = [
    "Command",
    "DeployRequestError",
    "FactCache",
    "HostRuntime",
    "OpContext",
    "OperationError",
    "OperationRequest",
    "Privilege",
    "UnhashableArgumentError",
    "WhenPredicate",
    "compose",
    "op",
    "sub_deploy",
]
