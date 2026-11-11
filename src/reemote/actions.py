# Copyright (c) 2026 Kim Jarvis.
# SPDX-License-Identifier: EPL-2.0

# """Actions: immediate execution, deliberately outside the deploy model.
#
# An action acts on the target servers *now* and hands back what happened. It
# exists for the pragmatic cases the library cannot serve: software on the
# user's servers that reemote knows nothing about, with no fact to read it and
# no operation to manage it (``Shell``), a script file already staged on the
# host that just needs running (``Script``, also reachable as ``server.script``
# from the server namespace), and connectivity itself -- waiting for
# hosts to come back after a reboot, between one deploy and the next
# (``WaitForConnection``).
#
# Actions are conceptually orthogonal to the prepare/apply model, and the split
# is enforced rather than conventional:
#
# - An action CANNOT run inside a deploy. Deploy generators are re-invoked per
#   phase (an action would execute twice), the prepare phase must never send
#   (an action would mutate the host mid-plan), and the determinism guard pairs
#   requests by value (an action has no plan-phase counterpart). ``gather_action``
#   therefore raises ``ActionInDeployError`` the moment it is *called* while a
#   deploy is being prepared or applied -- the check is synchronous, so even the
#   un-awaited call inside a sync deploy generator fails immediately. The flag
#   behind it is a ``ContextVar`` that only the drivers set (``_deploy_phase``
#   wraps ``run_prepare`` and ``run_apply``), keeping phase awareness in the
#   driver layer exactly as the model requires.
# - Users call actions BEFORE, BETWEEN and AFTER deploys. The awaited
#   ``gather_action`` is the barrier: every host's command has completed before
#   the next deploy starts or the next fact is read.
# - Composed deploys never contain an action; composition operates on
#   ``OperationRequest`` sequences, which actions are not.
#
# The shape mirrors fact gathering on purpose: ``Shell.process`` and
# ``WaitForConnection.process`` are single-host coroutines (like a fact's
# ``process``), and ``gather_action`` is the driver-level fan-out (like
# ``gather_fact``) returning a ``{host.name: value}`` mapping -- a ``Result``
# per host for ``Shell``, ``None`` for ``WaitForConnection`` (the key's presence
# is the success signal) -- gathered concurrently with ``asyncio.gather`` in
# input order. The fan-out also selects hosts: its ``group``/``tags`` keywords
# filter the inventory's static classifiers before anything runs, so an action
# can target a subset of the inventory exactly as a deploy branches on them.
#
# ``Result`` is a tiny library-owned ``TypedDict`` -- ``exit_status``, ``stdout``,
# ``stderr`` -- copied from the transport's own completion record. No asyncssh
# type reaches the public API, and the test framework can produce a ``Result``
# without importing asyncssh. The bespoke command's own exit status travels back
# in the result (even 127): the caller decides what a failure means. Library-level
# failures still RAISE: ``HostConnectionError`` for an unreachable host,
# ``PrivilegeError`` for doas's/sudo's/su's own authentication rejection, and
# ``ConnectionTimeoutError`` when ``WaitForConnection`` exhausts its timeout --
# each aggregated across hosts exactly as ``gather_fact`` and ``run_apply``
# aggregate them.
#
# Privilege escalation is honoured with plain ``doas``/``sudo``/``su`` bools -- the
# execution-layer spelling, not the plan layer's ``Privilege`` tri-state -- and
# the run-as users and passwords come from the inventory's ``privilege`` section,
# never from the call.
#
# Connection lifecycle: outside a deploy, each ``gather_action`` call re-connects.
# The fan-out closes every host's connection when it finishes (removing any
# askpass helpers), so the next call -- action or fact -- opens a fresh one.
# This keeps actions self-contained and leaves no long-lived state behind for
# future action kinds to inherit. ``WaitForConnection`` relies on it twice: it
# discards any cached connection up front (a pre-reboot one is stale), and the
# fan-out's teardown means the next deploy re-connects to the freshly booted
# hosts rather than inheriting the wait's sockets.
# """

import asyncio
import shlex
from collections.abc import Coroutine, Iterable
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any, TypedDict

from .hosts import Host, HostConnectionError, PrivilegeError

# Set only by the drivers (via ``_deploy_phase``) while a deploy is being
# prepared or applied. A ContextVar so the flag propagates into the per-host
# tasks ``asyncio.gather`` spawns, and resets cleanly even when a phase raises.
_deploy_active: ContextVar[bool] = ContextVar("reemote_deploy_active", default=False)


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


class ConnectionTimeoutError(RuntimeError):
    """Raised when ``WaitForConnection`` exhausts its timeout on a host.

    Fatal by design: the wait exists to guarantee every selected host is
    reachable before the next deploy starts, so a host that never came back
    must abort the script rather than let the deploy proceed against a dead
    machine. The message names the affected host(s) and the timeout that
    elapsed; the last per-host connection failure is preserved as ``__cause__``
    (and per host in ``failures``) for diagnosis.

    Because hosts are waited for concurrently, several may time out at once:
    ``gather_action`` merges the per-host errors into one aggregate via
    ``aggregate``, the same treatment ``run_prepare`` gives
    ``HostConnectionError``.

    ``failures`` is a tuple of ``(host_name, timeout_seconds, cause)`` triples.
    """

    def __init__(
        self, failures: Iterable[tuple[str, float, BaseException | None]]
    ) -> None:
        self.failures: tuple[tuple[str, float, BaseException | None], ...] = tuple(
            failures
        )
        hosts = ", ".join(name for name, _, _ in self.failures)
        label = "host" if len(self.failures) == 1 else "hosts"
        timeout = self.failures[0][1] if self.failures else 0.0
        super().__init__(
            f"timed out after {timeout:g}s waiting for a connection on "
            f"{len(self.failures)} {label}: {hosts}"
        )

    @property
    def host_names(self) -> tuple[str, ...]:
        """Names of the hosts that never became reachable within the timeout."""
        return tuple(name for name, _, _ in self.failures)

    @classmethod
    def aggregate(
        cls, errors: Iterable["ConnectionTimeoutError"]
    ) -> "ConnectionTimeoutError":
        """Merge several per-host errors into one naming every timed-out host."""
        collected = list(errors)
        failures = [failure for error in collected for failure in error.failures]
        merged = cls(failures)
        # Keep the first underlying connection error reachable for diagnosis.
        for _, _, cause in failures:
            if cause is not None:
                merged.__cause__ = cause
                break
        return merged


class Result(TypedDict):
    """
    Attributes:
        exit_status: The command's own exit code (``None`` when the transport
            reports none). Success is ``exit_status == 0``; a non-zero exit
            -- including 127, command not found -- is data here, not an
            exception.
        stdout: Captured standard output as text (bytes are decoded UTF-8).
        stderr: Captured standard error as text (bytes are decoded UTF-8).
    """

    # A library-owned ``TypedDict`` copy of the transport's completion record
    # (asyncssh's ``SSHCompletedProcess``): no asyncssh type leaks into the
    # public API, and the test framework can produce a ``Result`` without
    # importing asyncssh. The caller knows what their bespoke command's
    # failure means, so the outcome travels back whole instead of raising.

    exit_status: int | None
    stdout: str
    stderr: str


def _as_text(value: Any) -> str:
    """Normalize a transport output field to text (bytes decode UTF-8)."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


class Shell:
    """Run an arbitrary shell command on a host and return its ``Result``.

    Other Parameters:
        host (Host): The host to run on, supplied per host by
            ``gather_action``'s fan-out.
        command (str): The shell command text, sent verbatim -- nothing is
            interpreted or escaped.
        sudo (bool, optional): Elevate with ``sudo``, using the run-as user
            and password from the host's inventory ``privilege`` section.
            Defaults to False.
        su (bool, optional): Elevate with ``su``, using the run-as user and
            password from the host's inventory ``privilege`` section.
            Defaults to False.
        doas (bool, optional): Elevate with ``doas`` (passwordless ``doas
            -n``). The escalations combine in the fixed order ``doas`` then
            ``sudo`` then ``su``. Defaults to False.

    command:

    ```bash
    <command>
    ```

    return:

    ```python
    Result
    ```

    Examples:
        Examples can be found in ``examples/actions/action_shell.py`` and
        ``examples/actions/action_shell_sudo.py``.
    """

    # The parameters are documented as ``Other Parameters`` rather than
    # ``Args``: they are ``process``'s keywords (forwarded by
    # ``gather_action``), not constructor arguments, and griffe cross-checks
    # an ``Args`` section against the class signature -- which has none.
    # The first action kind. ``process`` mirrors a fact's single-host signature
    # so ``gather_action`` can fan it out identically, but unlike a fact it
    # interprets nothing: the command text is sent verbatim and the outcome
    # comes back whole. ``doas``/``sudo``/``su`` ask for elevation with the
    # host's inventory privilege data (run-as users, passwords via askpass
    # helpers, fixed ``doas`` then ``sudo`` then ``su`` order); a rejected
    # credential raises ``PrivilegeError`` rather than travelling back as an
    # exit status.

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        command: str,
        sudo: bool = False,
        su: bool = False,
        doas: bool = False,
    ) -> Result:
        """Execute ``command`` on ``host`` and capture its outcome.

        Rides the raw-result transport seams (``run_shell_result`` /
        ``run_shell_privileged_result``), so no exit status is interpreted on
        the way out. Connection and privilege failures still raise.
        """
        if sudo or su or doas:
            raw = await host.run_shell_privileged_result(
                command, sudo=sudo, su=su, doas=doas
            )
        else:
            raw = await host.run_shell_result(command)
        return Result(
            exit_status=raw.exit_status,
            stdout=_as_text(raw.stdout),
            stderr=_as_text(raw.stderr),
        )


class Script:
    """Run an existing script file on a host and return its ``Result``.

    The file must already be on the host -- ``Script`` never transfers it;
    stage it with ``files.put`` in a deploy first. A missing file is an error
    RESULT, not an exception: the wrapper exits 127 with a
    ``server.script: no such file`` message on stderr, read from the ``Result``
    like any other non-zero exit. Also reachable as ``server.script`` -- an
    alias in the ``server`` namespace for this same action class.

    Other Parameters:
        host (Host): The host to run on, supplied per host by
            ``gather_action``'s fan-out.
        path (str): Path of the script file on the host, used as given -- an
            absolute path is recommended, since a bare name would be looked
            up in ``PATH`` when the file is executed directly.
        args (Iterable[str], optional): Arguments passed to the script, each
            shell-quoted individually. A single string is rejected -- pass a
            sequence of arguments, not one joined string. Defaults to ().
        interpreter (str | None, optional): Explicit interpreter command,
            e.g. ``"bash"`` or ``"python3 -u"`` (split into words, each
            shell-quoted), run as ``<interpreter> <path> <args>`` regardless
            of the file's executable bit. The default ``None`` chooses per
            file: an executable file runs directly (the kernel honours its
            shebang), a non-executable file runs through ``sh``. Defaults
            to None.
        sudo (bool, optional): Elevate with ``sudo``, using the run-as user
            and password from the host's inventory ``privilege`` section.
            Defaults to False.
        su (bool, optional): Elevate with ``su``, using the run-as user and
            password from the host's inventory ``privilege`` section.
            Defaults to False.
        doas (bool, optional): Elevate with ``doas`` (passwordless ``doas
            -n``). The escalations combine in the fixed order ``doas`` then
            ``sudo`` then ``su``. Defaults to False.

    command:

    ```bash
    sh -c 'if [ ! -f <path> ]; then echo "server.script: no such file: <path>" >&2; exit 127; fi; if [ -x <path> ]; then <path> <args>; else sh <path> <args>; fi'
    ```

    return:

    ```python
    Result
    ```

    Examples:
        Examples can be found in ``examples/actions/action_script.py``.
    """

    # The ``server.script`` action: no transfer, no plan-phase counterpart,
    # the outcome travels back whole. The existence check, the executable-bit
    # branch and the invocation are ONE compound shell wrapper so a single
    # round-trip decides everything,
    # and the wrapper always rides an explicit ``sh -c``: sudo and doas exec
    # their command directly rather than through a shell, so a bare ``sudo if
    # ...`` would fail -- the ``sh -c`` carrier keeps the rendering identical
    # unprivileged and under any escalation. Execution itself delegates to
    # ``Shell.process``, so the raw-result seams, privilege handling and the
    # ``Result`` copy are shared, never duplicated.

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        path: str,
        args: Iterable[str] = (),
        interpreter: str | None = None,
        sudo: bool = False,
        su: bool = False,
        doas: bool = False,
    ) -> Result:
        """Execute the script at ``path`` on ``host`` and capture its outcome.

        Raises:
            TypeError: If ``args`` is a single string rather than an iterable
                of strings.
            ValueError: If ``interpreter`` is given but empty. A missing
                script file is NOT an exception -- it comes back as a
                ``Result`` with ``exit_status`` 127.
        """
        if isinstance(args, str):
            raise TypeError("args must be an iterable of strings, not one string")
        quoted_path = shlex.quote(path)
        arg_text = "".join(f" {shlex.quote(arg)}" for arg in args)
        if interpreter is not None:
            words = shlex.split(interpreter)
            if not words:
                raise ValueError("interpreter must be a non-empty command")
            invocation = (
                " ".join(shlex.quote(word) for word in words)
                + f" {quoted_path}{arg_text}"
            )
        else:
            # Executable: run it directly so its shebang picks the
            # interpreter; otherwise fall back to sh, which needs no
            # executable bit (only read permission).
            invocation = (
                f"if [ -x {quoted_path} ]; then {quoted_path}{arg_text}; "
                f"else sh {quoted_path}{arg_text}; fi"
            )
        missing = shlex.quote(f"server.script: no such file: {path}")
        wrapper = (
            f"if [ ! -f {quoted_path} ]; then echo {missing} >&2; exit 127; fi; "
            f"{invocation}"
        )
        command = f"sh -c {shlex.quote(wrapper)}"
        return await Shell.process(host, command=command, sudo=sudo, su=su, doas=doas)


class WaitForConnection:
    """Wait until a host accepts an SSH connection again, or time out fatally.

    Other Parameters:
        host (Host): The host to wait for, supplied per host by
            ``gather_action``'s fan-out.
        timeout (float, optional): Overall wait budget in seconds (must be
            positive). Each host gets its own budget, started when its wait
            begins; exhausting it raises the fatal ``ConnectionTimeoutError``.
            Defaults to 300.0.
        retry (float, optional): Pause in seconds between failed attempts
            (must be positive). The final pause is clamped so the wait never
            sleeps past its deadline. Defaults to 5.0.

    command:

    ```bash
    # none -- each attempt is a full SSH connection (Host.connect), no command runs
    ```

    return:

    ```python
    None
    ```

    Examples:
        Examples can be found in ``examples/actions/action_wait_for_connection.py``.
    """

    # The reboot companion to ``Shell``: after a deploy configures hosts and a
    # ``Shell`` action reboots them, this action waits for every selected host
    # to become reachable before the next deploy starts. ``process`` mirrors a
    # fact's single-host signature so ``gather_action`` fans it out identically;
    # all hosts are waited for concurrently, and the awaited gather is the
    # barrier -- it returns only once every host has connected.
    #
    # Each attempt rides ``Host.connect`` (the full SSH handshake and
    # authentication, not a bare TCP probe). Any cached connection is discarded
    # first: a pre-reboot one is stale, and ``connect`` would hand it back
    # without touching the network. A failed attempt -- ``HostConnectionError``
    # or an attempt that outlives the remaining timeout -- is retried after
    # ``retry`` seconds until the per-host ``timeout`` (seconds, measured from
    # the start of the wait) is exhausted; each attempt is itself bounded by the
    # time remaining, so a hanging handshake cannot outlive the timeout. On
    # exhaustion a fatal ``ConnectionTimeoutError`` is raised, aggregated across
    # hosts by ``gather_action``. Success returns ``None`` -- the host's key in
    # the gathered mapping is the success signal; no command is run.

    @classmethod
    async def process(
        cls,
        host: Host,
        *,
        timeout: float = 300.0,
        retry: float = 5.0,
    ) -> None:
        """Poll ``host.connect`` until it succeeds or ``timeout`` seconds elapse.

        Args:
            host: The host to wait for.
            timeout: Overall wait budget in seconds (must be positive). Each
                host gets its own budget, started when its wait begins.
            retry: Pause in seconds between failed attempts (must be
                positive). The final pause is clamped so the wait never
                sleeps past its deadline.

        Raises:
            ValueError: If ``timeout`` or ``retry`` is not positive.
            ConnectionTimeoutError: If no attempt succeeded within the
                timeout; the last connection failure is chained as its cause.
        """
        if timeout <= 0:
            raise ValueError("timeout must be a positive number of seconds")
        if retry <= 0:
            raise ValueError("retry must be a positive number of seconds")
        # Discard any cached connection before polling: after a reboot it is
        # stale, and Host.connect would return it without touching the network.
        aclose = getattr(host, "aclose", None)
        if aclose is not None:
            await aclose()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        last_error: BaseException | None = None
        while True:
            remaining = deadline - loop.time()
            try:
                # Bound the attempt itself so a hanging handshake cannot
                # outlive the overall timeout.
                async with asyncio.timeout(remaining):
                    await host.connect()
                return
            except (HostConnectionError, TimeoutError) as exc:
                last_error = exc
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ConnectionTimeoutError(
                    [(host.name, timeout, last_error)]
                ) from last_error
            await asyncio.sleep(min(retry, remaining))


def _select_hosts(
    hosts: Iterable[Host], group: str | None, tags: Iterable[str] | None
) -> list[Host]:
    """Filter hosts by the inventory's static ``group``/``tags`` classifiers.

    ``group`` selects hosts whose ``host.group`` equals it; ``tags`` selects
    hosts carrying EVERY listed tag. Both filters combine (AND), and ``None``
    means "no filter on this dimension". An empty selection is legal: the
    fan-out then runs nothing and returns an empty mapping.
    """
    required = frozenset(tags) if tags is not None else frozenset()
    return [
        host
        for host in hosts
        if (group is None or host.group == group) and required.issubset(host.tags)
    ]


def gather_action(
    hosts: Iterable[Host],
    action_cls: type,
    *args: Any,
    group: str | None = None,
    tags: Iterable[str] | None = None,
    **kwargs: Any,
) -> Coroutine[Any, Any, dict[str, Any]]:
    """Run one action across the selected hosts concurrently; await the dict.

    The action counterpart of ``gather_fact``: ``action_cls.process`` runs for
    every selected host at once via ``asyncio.gather`` and the awaited result
    maps ``host.name`` to that host's outcome, in input order -- a ``Result``
    for ``Shell``, ``None`` for ``WaitForConnection`` (the key's presence is
    the success signal). ``args``/``kwargs`` are forwarded verbatim
    (``command=``, ``sudo=``, ``su=``, ``doas=`` for ``Shell``; ``timeout=``,
    ``retry=`` for ``WaitForConnection``).
    The gather join is the barrier: every host's action has completed before
    the caller's next deploy or fact read begins.

    ``group`` and ``tags`` select which hosts run, from the inventory's static
    classifiers: ``group`` keeps hosts whose ``host.group`` equals it, ``tags``
    keeps hosts carrying EVERY listed tag, and the two combine (AND). ``None``
    (the default) means no filter on that dimension; an empty selection runs
    nothing and returns an empty mapping. The filters are consumed here, never
    forwarded to ``process``.

    This function is deliberately SYNCHRONOUS and returns the coroutine to
    await: the in-deploy check runs at call time, so calling it inside a deploy
    generator raises ``ActionInDeployError`` immediately -- even though a sync
    generator could never have awaited the coroutine anyway.

    Because hosts run concurrently, several may fail at once. Per-host
    ``HostConnectionError``, ``ConnectionTimeoutError`` and ``PrivilegeError``
    instances are collected and merged into one aggregate naming every
    affected host (connection failures reported first, mirroring
    ``run_prepare``); any other exception propagates unchanged. Every host's
    connection is closed when the call finishes -- each ``gather_action``
    call re-connects.
    """
    if _deploy_active.get():
        raise ActionInDeployError(
            f"{getattr(action_cls, '__name__', action_cls)} cannot run inside a "
            "deploy: actions execute immediately and would run once per phase, "
            "mutating hosts during prepare. Call gather_action before, between "
            "or after deploys instead."
        )
    return _gather_action(hosts, action_cls, *args, group=group, tags=tags, **kwargs)


async def _gather_action(
    hosts: Iterable[Host],
    action_cls: type,
    *args: Any,
    group: str | None = None,
    tags: Iterable[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """The concurrent fan-out ``gather_action`` hands back for awaiting."""
    host_list = _select_hosts(hosts, group, tags)
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
    timeout_errors: list[ConnectionTimeoutError] = []
    privilege_errors: list[PrivilegeError] = []
    for result in results:
        if isinstance(result, HostConnectionError):
            connection_errors.append(result)
        elif isinstance(result, ConnectionTimeoutError):
            timeout_errors.append(result)
        elif isinstance(result, PrivilegeError):
            privilege_errors.append(result)
        elif isinstance(result, BaseException):
            raise result
    if connection_errors:
        raise HostConnectionError.aggregate(connection_errors)
    if timeout_errors:
        raise ConnectionTimeoutError.aggregate(timeout_errors)
    if privilege_errors:
        raise PrivilegeError.aggregate(privilege_errors)
    return {host.name: value for host, value in zip(host_list, results)}


__all__ = [
    "ActionInDeployError",
    "ConnectionTimeoutError",
    "Result",
    "Script",
    "Shell",
    "WaitForConnection",
    "gather_action",
]
