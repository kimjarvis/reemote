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
- :func:`restart_handler` is the *handler factory*: it closes over that frozen
  set and returns a deploy that yields ``systemd.service(restarted=True)`` only
  for those hosts, so both phases resolve the branch identically and the
  determinism guard holds.

They compose directly::

    changed = changed_hosts(report, {"Install nginx", "Write nginx config"})
    restart = restart_handler(changed, "nginx", when=is_centos)
    await run(inventory, restart)

``changed`` is a fixed value by the time ``restart_handler`` builds the deploy,
so the returned generator is a pure function of static input. Deduplication and
end-of-run flushing come free -- at most one restart per host, after every
notifying operation has landed -- exactly like an Ansible handler, and a
converged re-run freezes an empty set so the handler deploy yields nothing.

The trade-offs mirror Ansible: the restart happens in a *second* full
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


def restart_handler(
    hosts: Iterable[str],
    services: str | Iterable[str],
    *,
    name: str = "Restart {service} (handler)",
    sudo: Privilege | bool = True,
    when: WhenPredicate | tuple[WhenPredicate, ...] | None = None,
) -> Callable[[Host], Generator[OperationRequest]]:
    """Build a handler deploy that restarts ``services`` on ``hosts`` only.

    The factory counterpart of :func:`changed_hosts`: it closes over the frozen
    set of notified host names and returns a deploy-shaped callable
    (``(host) -> generator of OperationRequest``), accepted anywhere a deploy
    function is -- ``run``, ``compose``, ``sub_deploy``. The returned generator
    branches on ``host.name in hosts``: an ordinary lookup on a snapshot taken
    *before* this deploy's prepare phase, so both phases resolve the branch
    identically and the determinism guard holds. Reading a live fact or a run
    outcome here instead would re-introduce the phase-dependent branch the
    guard rejects. An empty ``hosts`` yields an empty deploy -- no host
    restarts -- which is the converged no-op Ansible's handler gives for free.

    Each notified host gets one ``systemd.service(restarted=True)`` request per
    service. ``restarted`` only restarts a unit that is *already running*; the
    operation's default ``running=True`` starts a stopped one. So a first pass
    over a freshly-installed, stopped unit starts it, and a running unit is
    restarted -- matching the ``notify: restart <svc>`` idiom.

    Args:
        hosts (Iterable[str]): The host names to visit -- typically the
            frozenset ``changed_hosts`` returned. Any iterable is accepted and
            frozen here, so the closure captures an immutable value.
        services (str | Iterable[str]): One service name, or several. A bare
            name gets ``.service`` appended by the operation; any other systemd
            suffix (``.timer``, ...) is kept.
        name (str): A label template for each request's plan/log ``name``,
            formatted with ``service=``. The default renders e.g.
            ``"Restart nginx (handler)"``; a template without ``{service}`` is
            used verbatim (and would repeat across services on one host).
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
    """
    notified = frozenset(hosts)
    units = (services,) if isinstance(services, str) else tuple(services)

    def handler(host: Host) -> Generator[OperationRequest]:
        if host.name not in notified:
            return
        for service in units:
            yield op(
                systemd.service,
                name=name.format(service=service),
                service=service,
                restarted=True,
                sudo=sudo,
                when=when,
            )

    return handler


__all__ = [
    "changed_hosts",
    "restart_handler",
]
