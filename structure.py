"""
structure.py
============

A stand-alone, runnable demonstration of the design patterns used in
``reemote/execute.py``.

The SSH / inventory machinery is intentionally *not* implemented.  "Remote
command execution" is simulated in-process so this file can be run directly
with ``python structure.py`` and has no third-party dependencies.

Pattern map (demo -> counterpart in reemote/execute.py):

1. Command         -- ``Context`` encapsulates a request as data.
2. Factory         -- ``obj_factory`` creates a fresh command object per host.
3. Composite       -- command objects form a tree: ``Sequence`` nodes yield
                      children (``Shell`` leaves or other ``Sequence``s).
4. Iterator / IoC  -- ``pre_order_generator_async`` externalizes tree
                      traversal as an async generator driven through the
                      bidirectional ``yield`` / ``asend`` coroutine protocol.
5. Adapter         -- ``completed_process_to_dict`` converts simulated
                      ``CompletedProcess`` objects into plain dicts.
6. Facade          -- ``execute()`` hides orchestration behind one entry point.
7. Scatter-Gather  -- ``process_inventory`` executes each operation round on
                      all hosts concurrently (``asyncio.gather``); the gather
                      acts as a barrier, so operation N completes on every
                      host before operation N+1 starts (lockstep).
8. Null Object     -- hosts not matching a group produce ``None`` instead of
                      exceptions or special-casing; filtered downstream.
9. Blackboard      -- a shared per-run board accumulates every result indexed
                      by (command name, host name), so a command tree can read
                      ALL hosts' results for ANY executed Shell command -- not
                      just the latest round -- as soon as its ``yield`` resumes.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, AsyncGenerator, Callable, List, Tuple

# ---------------------------------------------------------------------------
# Simulated stand-ins for reemote.inventory / asyncssh
# ---------------------------------------------------------------------------


@dataclass
class SimulatedHost:
    """Stand-in for an inventory item: a name, group memberships, and a
    simulated network latency (to make the lockstep barrier observable)."""

    name: str
    groups: Tuple[str, ...] = ()
    latency: float = 0.05


@dataclass
class CompletedProcess:
    """Stand-in for ``asyncssh.SSHCompletedProcess``."""

    command: str
    exit_status: int
    returncode: int
    stdout: str
    stderr: str


def completed_process_to_dict(cp: CompletedProcess) -> dict:
    """ADAPTER: convert the process object into the shape the rest of the
    framework (response building / serialization) expects."""
    return {
        "command": cp.command,
        "exit_status": cp.exit_status,
        "returncode": cp.returncode,
        "stdout": cp.stdout,
        "stderr": cp.stderr,
    }


# ---------------------------------------------------------------------------
# Context & response types
# ---------------------------------------------------------------------------


class Method(Enum):
    """Response semantics, keyed by the operation's idempotency.

    GET  -- read-only query: returns a ``value`` and mutates nothing.
    POST -- non-idempotent action: every run mutates state, so ``changed``
            is not reported (it is implicitly True each time).
    PUT  -- idempotent action: reports ``changed`` to indicate whether this
            particular run actually mutated state (False on a no-op re-run).
    """

    GET = auto()
    POST = auto()
    PUT = auto()


@dataclass
class DemoResponse:
    """The response object built by ``get_result`` (``context.response``)."""

    host: str
    error: bool = False
    message: str = ""
    value: Any = None
    changed: bool = False
    request: Any = None


@dataclass
class Context:
    """COMMAND pattern: a request encapsulated as data, passed between the
    operation tree (producer) and the runner (executor).

    Mirrors ``reemote.context.Context``.
    """

    method: Method = Method.GET
    command: str = ""
    # Blackboard key: the Shell's dot-joined tree path (ancestor Sequence names
    # + the Shell name).  Filled in by the traversal, not by the node itself.
    path: str = ""
    group: str | None = None
    request_instance: Any = None
    response: Callable[..., Any] = DemoResponse
    # Populated by the driver (HostDriver) before execution:
    inventory_item: SimulatedHost | None = None
    value: Any = None
    error: bool = False
    changed: bool = False


def group_matches(context: Context) -> bool:
    """Same group-targeting rule as execute.py: no group, "all", or membership."""
    return (
        not context.group
        or "all" in context.group
        or context.group in context.inventory_item.groups
    )


def get_result(context: Context) -> Any:
    """Build the response, shaping its fields by ``context.method``.

    The method encodes idempotency: only PUT (idempotent) surfaces the
    ``changed`` indicator, because a non-idempotent POST always changes
    state and a read-only GET never does.
    """
    match context.method:
        case Method.GET:
            # Read-only query: carry the returned value.
            return context.response(
                host=context.inventory_item.name,
                error=context.error,
                message=context.value if context.error else "",
                value=context.value if not context.error else "",
                request=context.request_instance,
            )
        case Method.POST:
            # Non-idempotent action: success is implied; no value/changed.
            return context.response(
                host=context.inventory_item.name,
                error=context.error,
                message=context.value if context.error else "",
                request=context.request_instance,
            )
        case Method.PUT:
            # Idempotent action: report whether state actually changed.
            return context.response(
                host=context.inventory_item.name,
                error=context.error,
                message=context.value if context.error else "",
                changed=context.changed,
                request=context.request_instance,
            )
        case _:
            raise ValueError(f"Unsupported context method: {context.method}")


# ---------------------------------------------------------------------------
# Runner -- remote command execution
# ---------------------------------------------------------------------------


async def run_operation(context: Context) -> Any | None:
    """The "remote command" runner.

    In reemote this opens an SSH connection (with sudo/su handling); here the
    execution is simulated in-process.
    """
    if not group_matches(context):
        return None
    logging.info(f"{context.inventory_item.name:<16} - {context.command}")
    await asyncio.sleep(context.inventory_item.latency)  # simulated latency

    if "fail" in context.command:  # simulate a non-zero exit status
        cp = CompletedProcess(context.command, 1, 1, "", "simulated failure")
    else:
        cp = CompletedProcess(
            command=context.command,
            exit_status=0,
            returncode=0,
            stdout=f"[{context.inventory_item.name}] output of: {context.command}",
            stderr="",
        )

    context.value = completed_process_to_dict(cp)  # ADAPTER
    # A successful PUT (idempotent) operation mutates host state, so this is
    # the honest place to report `changed` -- an OPERATION runs on the host.
    if context.method is Method.PUT and context.value["returncode"] == 0:
        context.changed = True
    result = get_result(context)
    if context.value["returncode"] != 0:
        raise RuntimeError(f"non-zero return code: {context.value['stderr']}")
    return result


async def run_callback(context: Context) -> Any | None:
    """The local "imperative processing" runner for a CALLBACK node.

    Unlike ``run_operation`` this performs NO remote command: it invokes the
    node's callable in-process for the target host, so result-processing can
    live in the tree as a first-class node.  The callable receives the host and
    its (possibly awaitable) return value becomes the response ``value``.  The
    result is recorded to the blackboard under the Callback's tree path in
    exactly the same way as a Shell's.  Group filtering (NULL OBJECT) applies
    just as for ``run_operation``: a non-matching host records ``None``.
    """
    if not group_matches(context):
        return None
    logging.info(f"{context.inventory_item.name:<16} - callback:{context.path}")
    value = context.request_instance["callback"](context.inventory_item)
    if inspect.isawaitable(value):
        value = await value
    context.value = value
    return get_result(context)


# ---------------------------------------------------------------------------
# Tree traversal -- COMPOSITE + ITERATOR + coroutine (IoC) driver
# ---------------------------------------------------------------------------


async def pre_order_generator_async(
    node: object,
) -> AsyncGenerator[Context | Any, Any | None]:
    """Async pre-order traversal of the composite operation tree.

    This is a near-verbatim structural copy of execute.py's traversal:
    an explicit stack of ``(node, async_generator, send_value)`` frames.
    The generator *yields work items upward* (Context objects) and *receives
    execution results back* via ``asend`` -- inversion of control: the tree
    defines the workflow, the driver performs the I/O.
    """
    stack: List[Tuple[Any, AsyncGenerator, Any]] = []
    # Every node's tree PATH must be unique, so a name need only be unique
    # among its SIBLINGS (e.g. "a.setup" and "b.setup" may coexist).  Enforced
    # here because the traversal is the only place that visits EVERY node -- a
    # Sequence never surfaces as a Context, so the driver cannot see its name.
    seen_paths: set[str] = set()

    def frame_path() -> str:
        """Dot-joined names of the named nodes currently on the stack."""
        return ".".join(
            frame[0].name
            for frame in stack
            if getattr(frame[0], "name", None) is not None
        )

    if inspect.isasyncgen(node):
        # Root supplied directly as an async generator (a plain function root,
        # e.g. demo_script(board)).  It has no name, so frame_path skips it.
        stack.append((None, node, None))
    elif hasattr(node, "execute") and callable(node.execute):
        if inspect.isasyncgenfunction(node.execute):
            stack.append((node, node.execute(), None))
        else:
            # Regular coroutine node: run it and signal completion.
            await node.execute()
            yield None
            return
    else:
        raise TypeError(
            f"Root must be an async generator or have execute(): {type(node)}"
        )

    while stack:
        current_node, generator, send_value = stack[-1]
        try:
            if send_value is None:
                value = await generator.__anext__()
            else:
                value = await generator.asend(send_value)

            if isinstance(value, Context):
                # A unit of work (a Shell or Callback leaf).  Its blackboard key
                # is the tree PATH: the dot-joined names of every ancestor
                # Sequence plus the leaf itself (the root has no name, so
                # frame_path skips it).
                value.path = frame_path()
                # Hand it to the driver, await its result.
                result = yield value
                stack[-1] = (current_node, generator, result)

            elif hasattr(value, "execute") and callable(value.execute):
                # COMPOSITE: a nested node (Shell, Callback or Sequence) --
                # descend into it, first enforcing that its full tree PATH is
                # unique (names need only differ among siblings).
                name = getattr(value, "name", None)
                if name is not None:
                    parent_path = frame_path()
                    node_path = f"{parent_path}.{name}" if parent_path else name
                    if node_path in seen_paths:
                        raise ValueError(
                            f"duplicate tree path {node_path!r} in command tree; "
                            "sibling Shell, Callback and Sequence names must be "
                            "unique"
                        )
                    seen_paths.add(node_path)
                nested_execute = value.execute()
                if inspect.isasyncgenfunction(value.execute):
                    stack.append((value, nested_execute, None))
                else:
                    result = await nested_execute
                    stack[-1] = (current_node, generator, result)

            else:
                raise TypeError(
                    f"Unsupported yield type from async generator: {type(value)}"
                )

        except StopAsyncIteration as e:
            # Node finished: pop it and send its return value to the parent.
            return_value = e.value if hasattr(e, "value") else send_value
            stack.pop()
            if stack:
                stack[-1] = (stack[-1][0], stack[-1][1], return_value)

        except Exception as e:
            logging.error(f"{e}", exc_info=True)
            raise


# ---------------------------------------------------------------------------
# Command tree nodes: Shell / Callback leaves and the Sequence composite
# ---------------------------------------------------------------------------


class Shell:
    """Leaf OPERATION node -- stands in for reemote's shell command op.

    ``method`` selects the response shape (see ``Method``): GET returns the
    command output as ``value``; PUT reports whether the command ``changed``
    the host.  ``name`` is REQUIRED and may not contain a dot '.'; it must be
    unique among this node's siblings (Shell, Callback and Sequence names share
    one namespace at each level), so that every node's tree PATH is unique.  The
    name is the leaf segment of that path; the blackboard key is the full path
    (dot-joined ancestor Sequence names + this name), which is why a name may
    not itself contain a dot.
    """

    def __init__(
        self,
        command: str,
        *,
        name: str,
        group: str | None = None,
        method: Method = Method.GET,
    ):
        if "." in name:
            raise ValueError(f"Shell name {name!r} may not contain a dot '.'")
        self.command = command
        self.name = name
        self.group = group
        self.method = method

    async def execute(self):
        # Note: an async generator cannot `return` a value; the result sent
        # back via asend is propagated to the parent by the traversal's
        # send_value fallback when this generator completes.
        yield Context(
            method=self.method,
            command=self.command,
            group=self.group,
            request_instance={"command": self.command},
        )


class Callback:
    """Leaf node for IMPERATIVE result-processing -- runs local Python, not a
    remote command.

    A Callback exists so that processing which is not a host OPERATION can
    still live in the tree as a node: its callable runs once per host in
    lockstep, and its return value is recorded to the blackboard under the
    node's tree path exactly like a Shell's result.  The callable receives the
    target ``SimulatedHost`` and may close over the shared ``Blackboard`` to
    read earlier rounds; it may be sync or async (an awaitable return is
    awaited by ``run_callback``).

    ``name`` follows the same rules as Shell and Sequence: REQUIRED, may not
    contain a dot '.', and must be unique among this node's siblings (Shell,
    Callback and Sequence names share one namespace at each level), so every
    node's tree PATH stays unique.  ``method`` selects the response shape and
    ``group`` targets hosts, both exactly as for Shell.
    """

    def __init__(
        self,
        callback: Callable[..., Any],
        *,
        name: str,
        group: str | None = None,
        method: Method = Method.GET,
    ):
        if "." in name:
            raise ValueError(f"Callback name {name!r} may not contain a dot '.'")
        self.callback = callback
        self.name = name
        self.group = group
        self.method = method

    async def execute(self):
        # Yields a Context carrying the callable instead of a command; the
        # driver dispatches it to run_callback (see HostDriver.run_step).
        yield Context(
            method=self.method,
            group=self.group,
            request_instance={"callback": self.callback},
        )


class Sequence:
    """COMPOSITE node: runs children in order, collecting their results.

    Children may be leaves (``Shell`` or ``Callback``) or other composites --
    the traversal treats any node with an execute() async generator
    uniformly, so nesting works to arbitrary depth.  ``name`` is REQUIRED, may
    not contain a dot '.', and must be unique among this node's siblings (it
    shares one namespace with Shell and Callback names at each level), so every
    node's tree PATH is unique -- enforced by the traversal.  Each Sequence name
    becomes a segment in the blackboard PATH of every leaf beneath it.
    """

    def __init__(self, *children: Any, name: str):
        if "." in name:
            raise ValueError(f"Sequence name {name!r} may not contain a dot '.'")
        self.children = children
        self.name = name

    async def execute(self):
        results = []
        for child in self.children:
            results.append((yield child))
        logging.info(f"{self.name} collected {len(results)} child results")


# ---------------------------------------------------------------------------
# Callback callables -- imperative result-processing, defined at MODULE scope
# ---------------------------------------------------------------------------


def uname_a_check(host: SimulatedHost, board: Blackboard) -> Any:
    """Callback callable for the ``uname -a`` result (path ``outer.detect-os-full``).

    Defined OUTSIDE ``demo_script`` on purpose: a module-level callable cannot
    close over ``board``, so the board is a parameter, bound at the Callback
    site inside ``demo_script`` via ``lambda host: uname_a_check(host, board)``.
    It runs once per host (in lockstep), reads that host's ``outer.detect-os-full``
    result off the shared board -- the ``uname -a`` Shell nested just above it in
    the ``outer`` Sequence -- and derives a small summary: imperative
    result-processing living in a node.
    """
    resp = board.results["outer.detect-os-full"].get(host.name)
    if resp is None:  # NULL OBJECT: host was group-filtered for detect-os-full
        return None
    full = resp.value["stdout"]
    return {"full": full, "words": len(full.split())}


# ---------------------------------------------------------------------------
# Reusable Sequence factories -- PROGRAM COMPOSITION
# ---------------------------------------------------------------------------


def make_system_probe(name: str, board: Blackboard) -> Sequence:
    """FACTORY: returns a reusable Sequence that probes system info.

    The Sequence runs ``whoami`` (current user) and then a Callback that
    reads the result off the blackboard and derives a summary.

    Parameterized by ``name`` so it can be reused multiple times in one tree
    (each instance gets a unique blackboard path: ``{name}.whoami``, etc.).

    Parameterized by ``board`` because the Callback needs to read earlier
    results off the blackboard, and ``board`` doesn't exist at module scope.
    """

    def user_check(host: SimulatedHost) -> dict[str, Any]:
        """Callback: reads this host's ``whoami`` result and derives a summary."""
        resp = board.results[f"{name}.whoami"].get(host.name)
        if resp is None:  # NULL OBJECT: host was group-filtered
            return None
        user = resp.value["stdout"].strip()
        return {"user": user, "length": len(user)}

    return Sequence(
        Shell("whoami", group="linux", name="whoami"),
        Callback(user_check, group="linux", name="user-check"),
        name=name,
    )


def make_apt_install_probe(name: str, board: Blackboard) -> Sequence:
    """FACTORY: returns a reusable Sequence that installs a package and checks
    if the installed package list changed.

    The Sequence:
    1. Runs ``apt list --installed`` to capture the initial state.
    2. Runs ``apt-get install -y cowsay`` to install something.
    3. Runs ``apt list --installed`` again to capture the final state.
    4. Runs a Callback that compares the two lists and determines if they changed.

    Parameterized by ``name`` (for blackboard path uniqueness) and ``board``
    (because the Callback needs to read results from rounds N and N+2).
    """

    def check_changed(host: SimulatedHost) -> dict[str, Any]:
        """Callback: compares the two ``apt list --installed`` results."""
        before_resp = board.results[f"{name}.list-before"].get(host.name)
        after_resp = board.results[f"{name}.list-after"].get(host.name)
        if before_resp is None or after_resp is None:
            return None  # NULL OBJECT: host was group-filtered
        before = before_resp.value["stdout"]
        after = after_resp.value["stdout"]
        changed = before != after
        return {
            "changed": changed,
            "before_lines": len(before.strip().splitlines()),
            "after_lines": len(after.strip().splitlines()),
        }

    return Sequence(
        Shell("apt list --installed", group="linux", name="list-before"),
        Shell("apt-get install -y cowsay", group="linux", name="install"),
        Shell("apt list --installed", group="linux", name="list-after"),
        Callback(check_changed, group="linux", name="check"),
        name=name,
    )


# ---------------------------------------------------------------------------
# The root command generator (built by the FACTORY, once per host)
# ---------------------------------------------------------------------------


async def demo_script(board: Blackboard):
    """Root of the composite tree -- analogous to a reemote endpoint command.

    A plain async generator FUNCTION: each call returns a fresh generator, so
    the FACTORY still builds a new root per host, and ``board`` is a simple
    parameter rather than instance state.  The only state shared between hosts
    is the Blackboard, which the orchestrator fills with every host's result
    after each lockstep round.

    Three node types exist: ``Shell`` (a remote OPERATION leaf), ``Callback``
    (a leaf that runs imperative local processing) and ``Sequence`` (a
    COMPOSITE that runs its children in order).
    """
    # OPERATION leaf, targeted at the "linux" group only.  On non-matching
    # hosts run_operation returns None (NULL OBJECT).
    yield Shell("uname -s", group="linux", name="detect-os")

    # Imperative result-processing, now IN A NODE.  A Callback runs local
    # Python for each host (in lockstep) and records its return value to the
    # blackboard under its own tree path -- exactly like a Shell -- but it
    # performs NO remote OPERATION.  This one reads its host's round-1
    # detect-os result off the shared board (safe: the barrier guarantees
    # every host recorded before this round began).
    def uname_of(host):
        resp = board.results["detect-os"].get(host.name)
        return resp.value["stdout"] if resp is not None else None

    yield Callback(uname_of, group="linux", name="uname")

    # Like every node, the Callback's per-host results are on the board under
    # its path; roll them up here (a Callback runs per host, so cross-host
    # aggregation is a read of the board, indexed by host name).
    uname_by_host = {
        host: response.value
        for host, response in board.results["uname"].items()
        if response is not None
    }
    logging.info(f"uname results by host: {uname_by_host}")

    # A PUT operation mutates host state, so its response reports
    # `changed` -- the honest home for that flag (an OPERATION actually
    # runs on the host).  Collect which hosts changed.
    yield Shell(
        "touch /tmp/reemote-demo", method=Method.PUT, group="linux", name="mark"
    )
    changed_hosts = [
        host
        for host, response in board.results["mark"].items()
        if response is not None and response.changed
    ]
    logging.info(
        f"operation changed any host: {bool(changed_hosts)} "
        f"(changed hosts: {changed_hosts})"
    )

    # PROGRAM COMPOSITION: a factory returns a reusable Sequence.  The factory
    # takes ``name`` (for blackboard path uniqueness) and ``board`` (because the
    # Callback inside needs to read results).  This demonstrates how to build
    # reusable Sequence components that can be composed into larger trees.
    yield make_system_probe("probe", board)

    # PROGRAM COMPOSITION: another factory-produced Sequence, this one capturing
    # the installed-package list before/after an install, with a Callback
    # comparing them.  The Callback reads results from rounds N and N+2 (skipping
    # the install round), demonstrating cross-round blackboard reads.
    yield make_apt_install_probe("apt-install", board)

    # A Callback that prints whether the apt-install changed the package list.
    # It reads the apt-install.check result (from the round just above) and
    # prints "changed" or "not changed" per host.
    def print_apt_changed(host):
        check_resp = board.results["apt-install.check"].get(host.name)
        if check_resp is None:
            return  # NULL OBJECT: host was group-filtered
        changed = check_resp.value["changed"]
        print(f"{host.name}: {'changed' if changed else 'not changed'}")

    yield Callback(print_apt_changed, group="linux", name="print-apt-changed")

    # Nested COMPOSITEs: a Sequence accepts Shell leaves or other Sequence
    # nodes to any depth.  Below is 3 levels deep (root -> outer -> inner
    # -> innermost).
    yield Sequence(
        Shell("echo step-1", name="step-1"),
        Sequence(
            Shell("echo step-2", name="step-2"),
            Sequence(
                Shell("echo step-3", name="step-3"),
                Shell("echo step-4", name="step-4"),
                name="innermost",
            ),
            name="inner",
        ),
        # OPERATION leaf moved INSIDE the composite so `uname -a` runs here --
        # between step-4 (the end of inner) and step-5 -- immediately before the
        # Callback that consumes it (a Callback can only read a result already
        # on the board).  Its blackboard key is now the dot-joined path
        # "outer.detect-os-full" (it was the top-level "detect-os-full").
        Shell("uname -a", group="linux", name="detect-os-full"),
        # CALLBACK leaf nested in the composite: runs the module-level
        # uname_a_check per host, reading this host's detect-os-full result
        # recorded the round just above.  Bound to `board` via the lambda (a
        # module-level function cannot close over it).  Blackboard key becomes
        # "outer.uname-a-check".
        Callback(
            lambda host: uname_a_check(host, board),
            group="linux",
            name="uname-a-check",
        ),
        Shell("echo step-5", name="step-5"),
        name="outer",
    )
    # The board is indexed by (tree path, host), so results from EARLIER
    # rounds persist: after all the nested Sequences have run, every Shell
    # command's per-host results are still available for examination.
    logging.info(
        f"blackboard holds {len(board.results)} command paths: "
        f"{sorted(board.results)}"
    )
    logging.info(
        "detect-os (round 1) still available: "
        f"{sorted(board.results['detect-os'])}"
    )
    # Note: the root's return value is discarded by the driver; results are
    # read from the blackboard, indexed by tree path and host.


# ---------------------------------------------------------------------------
# Host & inventory orchestration -- LOCKSTEP scatter-gather
# ---------------------------------------------------------------------------


class Blackboard:
    """BLACKBOARD pattern: accumulates every leaf node's per-host results.

    Indexed by the node's tree PATH, then host name -- ``results[path][host]``
    -- so the results of ALL executed Shell and Callback nodes stay available
    for examination, not just the most recent lockstep round.  The path is the
    dot-joined names of the ancestor Sequences plus the leaf (e.g.
    ``"outer.inner.step-2"``); node names may not contain a dot precisely so
    this key stays unambiguous.

    Safety: ``record`` is synchronous (no ``await``), and the barrier in
    ``process_inventory`` guarantees every host has recorded a round's result
    before any command tree resumes to read it -- so reads never race writes.
    """

    def __init__(self) -> None:
        # tree path -> {host name -> result}
        self.results: dict[str, dict[str, Any]] = {}

    def record(self, path: str, host_name: str, result: Any) -> None:
        self.results.setdefault(path, {})[host_name] = result


class HostDriver:
    """Drives ONE host's operation tree, one step at a time.

    Splitting the per-host loop into ``start()`` / ``run_step()`` /
    ``advance()`` phases lets the orchestrator interpose a barrier between
    steps: every host executes the same round's operation concurrently, and
    no tree moves on until all hosts have finished that round.

    FACTORY: the root command generator is created fresh per host by the caller.
    """

    def __init__(self, host: SimulatedHost, root: Any, board: Blackboard):
        self.host = host
        self.board = board
        self.gen = pre_order_generator_async(root)
        self.context: Any = None
        self.result: Any = None
        self.done = False

    async def start(self) -> None:
        """Prime the generator: obtain the first Context (or finish)."""
        try:
            self.context = await self.gen.__anext__()
        except StopAsyncIteration:
            self.done = True

    async def run_step(self) -> None:
        """Execute this round's operation on the host."""
        if not isinstance(self.context, Context):
            raise TypeError(
                f"Unsupported type from async generator: {type(self.context)}"
            )
        self.context.inventory_item = self.host
        # Dispatch by node kind: a Callback runs local imperative processing, a
        # Shell runs a (simulated) remote command.  Both record to the
        # blackboard the same way, under the node's tree path.
        runner = (
            run_callback
            if "callback" in self.context.request_instance
            else run_operation
        )
        self.result = await runner(self.context)
        # BLACKBOARD: publish this host's result, indexed by the node's tree
        # path and host name (may be None -- NULL OBJECT for group-filtered
        # hosts).
        self.board.record(self.context.path, self.host.name, self.result)

    async def advance(self) -> None:
        """Feed the result back into the tree and pull the next Context."""
        try:
            self.context = await self.gen.asend(self.result)
        except StopAsyncIteration:
            self.done = True


async def process_inventory(
    hosts: List[SimulatedHost],
    root_obj_factory: Callable[[Blackboard], Any],
) -> None:
    """LOCKSTEP SCATTER-GATHER (a barrier per operation).

    Each round:
      1. scatter -- run the current operation on all active hosts
         concurrently (``asyncio.gather``);
      2. barrier -- the gather itself is the barrier: no host's tree moves
         on until every host has finished this operation;
      3. advance -- feed each result back into its host's generator and
         pull the next Context.

    Hosts whose tree is exhausted drop out of later rounds; because every
    host's tree comes from the same factory, the Context sequences (and
    therefore the rounds) stay aligned -- group filtering produces a None
    result for a round, it does not skip the round.
    """
    if not hosts:
        return

    # BLACKBOARD: one shared board per run, accumulating every command's
    # per-host results.  FACTORY: a fresh root generator per host, with
    # the board bound in so trees can read all hosts' results.
    board = Blackboard()
    drivers = [HostDriver(host, root_obj_factory(board), board) for host in hosts]

    # Prime all trees concurrently.
    await asyncio.gather(*(d.start() for d in drivers))

    # Lockstep rounds until every tree is exhausted.
    round_no = 0
    while True:
        active = [d for d in drivers if not d.done]
        if not active:
            break
        round_no += 1
        logging.info(f"--- round {round_no}: {len(active)} host(s) ---")
        # 1+2. run this round's operation on all active hosts, wait for all.
        await asyncio.gather(*(d.run_step() for d in active))
        # 3. only now advance every tree to its next operation.
        await asyncio.gather(*(d.advance() for d in active))


# ---------------------------------------------------------------------------
# Facade & demo entry point
# ---------------------------------------------------------------------------


async def execute(
    root_obj_factory: Callable[[Blackboard], Any],
    inventory: List[SimulatedHost],
) -> None:
    """FACADE: a single entry point hiding logging setup and orchestration
    (cf. execute()/endpoint_execute())."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    await process_inventory(inventory, root_obj_factory)


async def main() -> None:
    # A tiny simulated "inventory".
    inventory = [
        SimulatedHost("web-1", ("linux", "webservers"), latency=0.15),
        SimulatedHost("web-2", ("linux", "webservers"), latency=0.02),
        SimulatedHost("win-1", ("windows",), latency=0.08),
    ]

    # FACTORY: the caller supplies how to build the root command generator;
    # the shared blackboard is passed in so trees can read cross-host results.
    await execute(lambda board: demo_script(board), inventory)


if __name__ == "__main__":
    asyncio.run(main())
