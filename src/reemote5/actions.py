# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

"""Actions: immediate shell execution, deliberately outside the deploy model.

An action runs a bespoke command on the target servers *now* and hands back
what the command produced. It exists for the pragmatic case the library cannot
serve: software on the user's servers that reemote5 knows nothing about, with
no fact to read it and no operation to manage it.

Actions are conceptually orthogonal to the prepare/apply model, and the split
is enforced rather than conventional:

- An action CANNOT run inside a deploy. Deploy generators are re-invoked per
  phase (an action would execute twice), the prepare phase must never send
  (an action would mutate the host mid-plan), and the determinism guard pairs
  requests by value (an action has no plan-phase counterpart). ``gather_action``
  therefore raises ``ActionInDeployError`` the moment it is *called* while a
  deploy is being prepared or applied -- the check is synchronous, so even the
  un-awaited call inside a sync deploy generator fails immediately. The flag
  behind it is a ``ContextVar`` that only the drivers set (``_deploy_phase``
  wraps ``run_prepare`` and ``run_apply``), keeping phase awareness in the
  driver layer exactly as the model requires.
- Users call actions BEFORE, BETWEEN and AFTER deploys. The awaited
  ``gather_action`` is the barrier: every host's command has completed before
  the next deploy starts or the next fact is read.
- Composed deploys never contain an action; composition operates on
  ``OperationRequest`` sequences, which actions are not.

The shape mirrors fact gathering on purpose: ``Shell.process`` is a
single-host coroutine (like a fact's ``process``), and ``gather_action`` is the
driver-level fan-out (like ``gather_fact``) returning a ``{host.name: Result}``
mapping, gathered concurrently with ``asyncio.gather`` in input order.

``Result`` is a tiny library-owned frozen record -- ``exit_status``, ``stdout``,
``stderr`` -- copied from the transport's own completion record. No asyncssh
type reaches the public API, and the test framework can produce a ``Result``
without importing asyncssh. The bespoke command's own exit status travels back
in the result (even 127): the caller decides what a failure means. Library-level
failures still RAISE: ``HostConnectionError`` for an unreachable host and
``PrivilegeError`` for sudo's/su's own authentication rejection, each aggregated
across hosts exactly as ``gather_fact`` and ``run_apply`` aggregate them.

Privilege escalation is honoured with plain ``sudo``/``su`` bools -- the
execution-layer spelling, not the plan layer's ``Privilege`` tri-state -- and
the run-as users and passwords come from the inventory's ``privilege`` section,
never from the call.

Connection lifecycle: outside a deploy, each ``gather_action`` call re-connects.
The fan-out closes every host's connection when it finishes (removing any
askpass helpers), so the next call -- action or fact -- opens a fresh one.
This keeps actions self-contained and leaves no long-lived state behind for
future action kinds to inherit.
"""

import asyncio
from collections.abc import Coroutine, Iterable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Any

from .hosts import Host, HostConnectionError, PrivilegeError

# Set only by the drivers (via ``_deploy_phase``) while a deploy is being
# prepared or applied. A ContextVar so the flag propagates into the per-host
# tasks ``asyncio.gather`` spawns, and resets cleanly even when a phase raises.
_deploy_active: ContextVar[bool] = ContextVar("reemote5_deploy_active", default=False)


@contextmanager
def _in_deploy():
    """Mark the enclosed execution as inside a deploy (drivers only)."""
    token = _deploy_active.set(True)
    try:
        yield
    finally:
        _deploy_active.reset(token)


def _deploy_phase(func):
    """Wrap a driver coroutine so the in-deploy guard holds for its duration.

    Applied by ``drivers`` to ``run_prepare`` and ``run_apply`` -- the two
    places deploy generators are invoked or advanced. The mechanism lives
    here beside the check it feeds, but only the drivers toggle it, so facts,
    operations and actions themselves stay phase-unaware.
    """

    @wraps(func)
    async def wrapper(*args, **kwargs):
        with _in_deploy():
            return await func(*args, **kwargs)

    return wrapper


class ActionInDeployError(RuntimeError):
    """Raised when an action is called while a deploy is running.

    Actions execute immediately and cannot be replayed, so they have no place
    in a deploy generator: prepare must never send, the generator runs once
    per phase, and the determinism guard pairs requests by value. The error is
    raised synchronously at the ``gather_action`` call site -- before any
    coroutine is created -- so the misuse is reported where it happens, not
    silently swallowed as a never-awaited coroutine.
    """


@dataclass(frozen=True)
class Result:
    """The outcome of one action command on one host.

    A library-owned copy of the transport's completion record: ``exit_status``
    is the command's own exit code (``None`` when the transport reports none),
    ``stdout``/``stderr`` are the captured output as text (bytes are decoded
    UTF-8). No asyncssh type leaks into the public API, and a non-zero exit --
    including 127, command not found -- is data here, not an exception: the
    caller knows what their bespoke command's failure means.
    """

    exit_status: int | None
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """True when the command exited zero."""
        return self.exit_status == 0


def _as_text(value: Any) -> str:
    """Normalize a transport output field to text (bytes decode UTF-8)."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


class Shell:
    """Run an arbitrary shell command on a host and return its ``Result``.

    The first action kind. ``process`` mirrors a fact's single-host signature
    so ``gather_action`` can fan it out identically, but unlike a fact it
    interprets nothing: the command text is sent verbatim and the outcome
    comes back whole. ``sudo``/``su`` ask for elevation with the host's
    inventory privilege data (run-as users, passwords via askpass helpers,
    fixed ``sudo`` then ``su`` order); a rejected credential raises
    ``PrivilegeError`` rather than travelling back as an exit status.
    """

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        command: str,
        sudo: bool = False,
        su: bool = False,
    ) -> Result:
        """Execute ``command`` on ``host`` and capture its outcome.

        Rides the raw-result transport seams (``run_shell_result`` /
        ``run_shell_privileged_result``), so no exit status is interpreted on
        the way out. Connection and privilege failures still raise.
        """
        if sudo or su:
            raw = await host.run_shell_privileged_result(command, sudo=sudo, su=su)
        else:
            raw = await host.run_shell_result(command)
        return Result(
            exit_status=raw.exit_status,
            stdout=_as_text(raw.stdout),
            stderr=_as_text(raw.stderr),
        )


def gather_action(
    hosts: Iterable[Host], action_cls: type, *args: Any, **kwargs: Any
) -> Coroutine[Any, Any, dict[str, Result]]:
    """Run one action across all hosts concurrently; await the returned dict.

    The action counterpart of ``gather_fact``: ``action_cls.process`` runs for
    every host at once via ``asyncio.gather`` and the awaited result maps
    ``host.name`` to that host's ``Result``, in input order. ``args``/``kwargs``
    are forwarded verbatim (``command=``, ``sudo=``, ``su=`` for ``Shell``).
    The gather join is the barrier: every host's command has completed before
    the caller's next deploy or fact read begins.

    This function is deliberately SYNCHRONOUS and returns the coroutine to
    await: the in-deploy check runs at call time, so calling it inside a deploy
    generator raises ``ActionInDeployError`` immediately -- even though a sync
    generator could never have awaited the coroutine anyway.

    Because hosts run concurrently, several may be unreachable or reject
    elevation at once. Per-host ``HostConnectionError`` and ``PrivilegeError``
    instances are collected and merged into one aggregate naming every affected
    host (connection failures reported first, mirroring ``run_prepare``); any
    other exception propagates unchanged. Every host's connection is closed
    when the call finishes -- each ``gather_action`` call re-connects.
    """
    if _deploy_active.get():
        raise ActionInDeployError(
            f"{getattr(action_cls, '__name__', action_cls)} cannot run inside a "
            "deploy: actions execute immediately and would run once per phase, "
            "mutating hosts during prepare. Call gather_action before, between "
            "or after deploys instead."
        )
    return _gather_action(hosts, action_cls, *args, **kwargs)


async def _gather_action(
    hosts: Iterable[Host], action_cls: type, *args: Any, **kwargs: Any
) -> dict[str, Result]:
    """The concurrent fan-out ``gather_action`` hands back for awaiting."""
    host_list = list(hosts)
    try:
        results = await asyncio.gather(
            *(action_cls.process(host, *args, **kwargs) for host in host_list),
            return_exceptions=True,
        )
    finally:
        # Each call re-connects: tear down what this call opened (a no-op for
        # a host that never connected), removing any askpass helpers too.
        for host in host_list:
            aclose = getattr(host, "aclose", None)
            if aclose is not None:
                await aclose()
    connection_errors: list[HostConnectionError] = []
    privilege_errors: list[PrivilegeError] = []
    for result in results:
        if isinstance(result, HostConnectionError):
            connection_errors.append(result)
        elif isinstance(result, PrivilegeError):
            privilege_errors.append(result)
        elif isinstance(result, BaseException):
            raise result
    if connection_errors:
        raise HostConnectionError.aggregate(connection_errors)
    if privilege_errors:
        raise PrivilegeError.aggregate(privilege_errors)
    return {host.name: value for host, value in zip(host_list, results)}


__all__ = [
    "ActionInDeployError",
    "Result",
    "Shell",
    "gather_action",
]
