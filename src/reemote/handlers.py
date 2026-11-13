# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Reusable change-notification (handler) helpers.

Ansible roles close "restart the service only when something changed" with a
``notify:``/handler pair; reemote has **no first-class handler mechanism** (no
``notify`` keyword, no event bus), because a deploy body cannot observe its own
earlier outcomes -- ``ctx.changed`` flows one way (operation -> driver ->
``ApplyReport``) and branching on it inside one deploy would make the prepare
and apply phases yield different request sequences, tripping the determinism
guard (see ``drivers.DeterminismError``).

The sanctioned shape is the **two-deploy outcome split**: run the notifying
tasks, then read their ``ApplyReport`` in the gap *between* two complete
prepare/apply cycles, freeze the hosts that reported a change, and pass that
frozen set into a second deploy as a static input. This module packages the two
halves of that split so a role migration stops copy-pasting a bespoke
``notifying_hosts``/``build_restart_*`` pair:

- :func:`changed_hosts` is the *notification read*: one pass over a report's
  log, freezing the hosts whose named entries reported ``changed``.
- :func:`service_handler` is the *handler factory*: it closes over that frozen
  set and returns a deploy that yields one ``systemd.service`` request per
  service, for those hosts only, with an ``action`` selecting the verb --
  ``"restarted"``, ``"reloaded"`` or ``"running"`` -- so both phases resolve the
  branch identically and the determinism guard holds. :func:`restart_handler`
  and :func:`reload_handler` are thin wrappers pinning ``action`` to the two
  commonest idioms.

They compose directly::

    changed = changed_hosts(report, {"Install nginx", "Write nginx config"})
    reload = reload_handler(changed, "nginx", when=is_centos)
    await run(inventory, reload)

``changed`` is a fixed value by the time the factory builds the deploy, so the
returned generator is a pure function of static input. Deduplication and
end-of-run flushing come free -- at most one request per host per service, after
every notifying operation has landed -- exactly like an Ansible handler, and a
converged re-run freezes an empty set so the handler deploy yields nothing.

The trade-offs mirror Ansible: the handler's action happens in a *second* full
prepare/apply cycle, and a ``dry=True`` preview of the first run cannot know
whether the handler will fire (Ansible check mode skips handlers for the same
reason). A ``dry=True`` preview of the handler deploy *is* honest, because its
branch consults the frozen outcome of the first run, not pre-deployment state.
"""

from collections.abc import Callable, Generator, Iterable
from typing import TYPE_CHECKING

from .core import OperationRequest, Privilege, WhenPredicate, op
from .hosts import Host
from .modules import systemd

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from .drivers import ApplyReport


def changed_hosts(report: "ApplyReport", names: Iterable[str]) -> frozenset[str]:
    """Freeze the hosts whose *named* operations reported a change.

    This is the whole notification mechanism: a single pass over ``report``'s
    apply log, taken between two runs. An entry qualifies when it is
    ``changed`` **and** its ``name`` is one of ``names`` -- the analogue of
    Ansible's ``notify:`` list, where only the tasks tagged with a handler's
    name queue it. Skipped entries (a ``when`` guard failed) carry
    ``changed=False`` and never queue the handler; a ``changed`` entry is
    trustworthy, because the driver is fail-fast -- a non-zero command exit
    raises ``CommandError``, so ``changed`` means the commands really ran and
    succeeded.

    Args:
        report (ApplyReport): The apply report of the notifying run -- the
            value ``run`` returns as its second element (never ``None`` for a
            non-``dry`` run).
        names (Iterable[str]): The request names that queue the handler. A
            name matches an entry's ``name`` exactly; an unnamed request falls
            back to its operation function's name in the log, so name your
            notifying requests to select them here.

    Returns:
        frozenset[str]: The names of the hosts to notify -- immutable, so it
        can be closed over by a handler deploy and stay a stable, hashable
        input across both of that deploy's phases. Empty when nothing changed.
    """
    notifying = frozenset(names)
    return frozenset(
        entry.host for entry in report.log if entry.changed and entry.name in notifying
    )


#: The action vocabulary: each public action word maps to the verb used in the
#: default request name and the ``systemd.service`` keyword(s) it sets. Every
#: action relies on the operation's default ``running=True``, so a stopped unit
#: is started before (or instead of) the verb-specific action -- mirroring the
#: Ansible ``state:`` values ``restarted``, ``reloaded`` and ``started``.
_ACTIONS: dict[str, tuple[str, dict[str, bool]]] = {
    "restarted": ("Restart", {"restarted": True}),
    "reloaded": ("Reload", {"reloaded": True}),
    "running": ("Start", {}),
}


def service_handler(
    hosts: Iterable[str],
    services: str | Iterable[str],
    *,
    action: str = "restarted",
    name: str | None = None,
    sudo: Privilege | bool = True,
    when: WhenPredicate | tuple[WhenPredicate, ...] | None = None,
) -> Callable[[Host], Generator[OperationRequest]]:
    """Build a handler deploy that acts on ``services`` on ``hosts`` only.

    The general factory counterpart of :func:`changed_hosts`: it closes over the
    frozen set of notified host names and returns a deploy-shaped callable
    (``(host) -> generator of OperationRequest``), accepted anywhere a deploy
    function is -- ``run``, ``compose``, ``sub_deploy``. The returned generator
    branches on ``host.name in hosts``: an ordinary lookup on a snapshot taken
    *before* this deploy's prepare phase, so both phases resolve the branch
    identically and the determinism guard holds. Reading a live fact or a run
    outcome here instead would re-introduce the phase-dependent branch the
    guard rejects. An empty ``hosts`` yields an empty deploy -- no host is
    touched -- which is the converged no-op Ansible's handler gives for free.

    ``action`` selects the ``systemd.service`` verb, and therefore which Ansible
    ``state:`` the handler reproduces. Each notified host gets one request per
    service:

    - ``"restarted"`` (the default) sets ``restarted=True``: restart a unit that
      is *already running*. The operation's default ``running=True`` starts a
      stopped one, so a first pass over a freshly-installed, stopped unit starts
      it and a running unit is restarted -- ``state: restarted``.
    - ``"reloaded"`` sets ``reloaded=True``: reload a running unit (a stopped one
      is started instead) -- ``state: reloaded``, the reload-on-config-change
      idiom.
    - ``"running"`` sets neither flag, relying on the default ``running=True`` to
      start a stopped unit and leave a running one alone -- ``state: started``.

    Args:
        hosts (Iterable[str]): The host names to visit -- typically the
            frozenset ``changed_hosts`` returned. Any iterable is accepted and
            frozen here, so the closure captures an immutable value.
        services (str | Iterable[str]): One service name, or several. A bare
            name gets ``.service`` appended by the operation; any other systemd
            suffix (``.timer``, ...) is kept.
        action (str): Which ``systemd.service`` verb to issue -- ``"restarted"``,
            ``"reloaded"`` or ``"running"``. Defaults to ``"restarted"``.
        name (str | None): A label template for each request's plan/log ``name``,
            formatted with ``service=``. When ``None`` (the default) it is built
            from ``action`` -- e.g. ``"Restart nginx (handler)"``,
            ``"Reload nginx (handler)"`` or ``"Start nginx (handler)"``. A
            template without ``{service}`` is used verbatim (and would repeat
            across services on one host).
        sudo (Privilege | bool): The escalation flag forwarded to each request
            (``systemctl`` needs root, so it defaults to ``True``). Accepts a
            :class:`~reemote.core.Privilege` or a plain bool.
        when (WhenPredicate | tuple | None): An optional eligibility guard over
            *static* inventory data, forwarded to each request -- the same
            stable, module-level predicate rule every ``when`` follows, so both
            phases reach the same skip decision.

    Returns:
        Callable[[Host], Generator[OperationRequest]]: The handler deploy,
        pure over the frozen ``hosts`` set.

    Raises:
        ValueError: If ``action`` is not one of the three known verbs.
    """
    try:
        verb, flags = _ACTIONS[action]
    except KeyError:
        raise ValueError(
            f"unknown action {action!r}; expected one of "
            f"{', '.join(repr(a) for a in _ACTIONS)}"
        ) from None

    notified = frozenset(hosts)
    units = (services,) if isinstance(services, str) else tuple(services)
    label = name if name is not None else f"{verb} {{service}} (handler)"

    def handler(host: Host) -> Generator[OperationRequest]:
        if host.name not in notified:
            return
        for service in units:
            yield op(
                systemd.service,
                name=label.format(service=service),
                service=service,
                sudo=sudo,
                when=when,
                **flags,
            )

    return handler


def restart_handler(
    hosts: Iterable[str],
    services: str | Iterable[str],
    *,
    name: str | None = None,
    sudo: Privilege | bool = True,
    when: WhenPredicate | tuple[WhenPredicate, ...] | None = None,
) -> Callable[[Host], Generator[OperationRequest]]:
    """Build a handler deploy that restarts ``services`` on ``hosts`` only.

    A thin wrapper over :func:`service_handler` pinning ``action="restarted"``:
    each notified host gets one ``systemd.service(restarted=True)`` request per
    service. ``restarted`` only restarts a unit that is *already running*; the
    operation's default ``running=True`` starts a stopped one. So a first pass
    over a freshly-installed, stopped unit starts it, and a running unit is
    restarted -- matching the ``notify: restart <svc>`` idiom. See
    :func:`service_handler` for the full contract (the frozen-set branch,
    determinism, the converged no-op); ``name``, ``sudo`` and ``when`` are
    forwarded verbatim, and ``name`` defaults to ``"Restart {service}
    (handler)"``.

    Args:
        hosts (Iterable[str]): The host names to visit -- typically the
            frozenset ``changed_hosts`` returned.
        services (str | Iterable[str]): One service name, or several.
        name (str | None): A label template formatted with ``service=``; ``None``
            builds ``"Restart {service} (handler)"``.
        sudo (Privilege | bool): The escalation flag forwarded to each request.
        when (WhenPredicate | tuple | None): An optional static eligibility guard
            forwarded to each request.

    Returns:
        Callable[[Host], Generator[OperationRequest]]: The handler deploy,
        pure over the frozen ``hosts`` set.
    """
    return service_handler(
        hosts, services, action="restarted", name=name, sudo=sudo, when=when
    )


def reload_handler(
    hosts: Iterable[str],
    services: str | Iterable[str],
    *,
    name: str | None = None,
    sudo: Privilege | bool = True,
    when: WhenPredicate | tuple[WhenPredicate, ...] | None = None,
) -> Callable[[Host], Generator[OperationRequest]]:
    """Build a handler deploy that reloads ``services`` on ``hosts`` only.

    A thin wrapper over :func:`service_handler` pinning ``action="reloaded"``:
    each notified host gets one ``systemd.service(reloaded=True)`` request per
    service. ``reloaded`` only reloads a unit that is *already running*; the
    operation's default ``running=True`` starts a stopped one. This is the
    ``notify: reload <svc>`` idiom -- the reload-on-config-change half of a role
    that also restarts on other changes, so a config/vhost write can queue a
    reload while a package or default-vhost change queues a restart. See
    :func:`service_handler` for the full contract; ``name``, ``sudo`` and
    ``when`` are forwarded verbatim, and ``name`` defaults to ``"Reload
    {service} (handler)"``.

    Args:
        hosts (Iterable[str]): The host names to visit -- typically the
            frozenset ``changed_hosts`` returned.
        services (str | Iterable[str]): One service name, or several.
        name (str | None): A label template formatted with ``service=``; ``None``
            builds ``"Reload {service} (handler)"``.
        sudo (Privilege | bool): The escalation flag forwarded to each request.
        when (WhenPredicate | tuple | None): An optional static eligibility guard
            forwarded to each request.

    Returns:
        Callable[[Host], Generator[OperationRequest]]: The handler deploy,
        pure over the frozen ``hosts`` set.
    """
    return service_handler(
        hosts, services, action="reloaded", name=name, sudo=sudo, when=when
    )


__all__ = [
    "changed_hosts",
    "reload_handler",
    "restart_handler",
    "service_handler",
]
