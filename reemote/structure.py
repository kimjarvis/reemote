"""
structure.py
============

A stand-alone demonstration of the design patterns used in
``reemote/execute.py``.

The SSH / inventory machinery is intentionally *not* implemented.  "Remote
command execution" is simulated in-process, so this module has no third-party
dependencies.  The engine (Context, Shell/Callback/Sequence, traversal,
Blackboard, lockstep orchestration and the ``execute()`` facade) lives here;
the demonstration command tree (``demo_script`` and its factories) lives in
``tests/test_structure.py``.

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
# Custom exception for Callback-initiated aborts
# ---------------------------------------------------------------------------


class CallbackAbortError(Exception):
    """Raised by a Callback to abort the entire execution.

    Carries structured context -- the failing host name and the Callback's
    tree path -- so that ``execute()`` can log exactly which host and which
    Callback caused the abort, and can look up related blackboard entries
    for that host.
    """

    def __init__(self, host_name: str, callback_path: str, message: str = ""):
        self.host_name = host_name
        self.callback_path = callback_path
        self.message = message
        super().__init__(
            f"Callback '{callback_path}' aborted on host '{host_name}'"
            + (f": {message}" if message else "")
        )

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
class Response:
    """The response object built by ``get_result`` (``context.response``).

    Mirrors ``reemote.response`` -- the engine's per-host result value type.
    """

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
    response: Callable[..., Any] = Response
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
    execution is simulated in-process.  Errors (SSH failures, non-zero return
    codes) are CAUGHT and RECORDED as error results, not propagated -- this
    matches the real framework's "catch and record" pattern, where one host's
    failure doesn't crash the run for other hosts.
    """
    if not group_matches(context):
        return None
    logging.info(f"{context.inventory_item.name:<16} - {context.command}")
    await asyncio.sleep(context.inventory_item.latency)  # simulated latency

    try:
        # Simulate SSH errors for specific commands (demonstration)
        if context.command == "ssh-error":
            raise RuntimeError("Simulated SSH connection error")

        # Simulate the command execution
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

        # Non-zero return code: record as error (don't raise)
        if context.value["returncode"] != 0:
            context.error = f"non-zero return code: {context.value['stderr']}"
            context.value = {
                "error": context.error,
                "command": context.command,
                "returncode": context.value["returncode"],
            }

        return get_result(context)

    except Exception as e:
        # Catch SSH errors (simulated), record them as error results.
        # This matches the real framework's pattern: catch asyncssh errors,
        # convert to error results, continue execution for other hosts.
        context.error = str(e)
        context.value = {
            "error": str(e),
            "command": context.command,
        }
        return get_result(context)


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
    try:
        value = context.request_instance["callback"](context.inventory_item)
        if inspect.isawaitable(value):
            value = await value
    except CallbackAbortError:
        # Already wrapped (e.g. by a nested call) -- re-raise as-is.
        raise
    except Exception as e:
        # Wrap the exception with host name and callback path so execute()
        # can log structured context.
        raise CallbackAbortError(
            host_name=context.inventory_item.name,
            callback_path=context.path,
            message=str(e),
        ) from e
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
    board: Blackboard,
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

    # BLACKBOARD: the shared board is passed in by the caller, accumulating
    # every command's per-host results.  FACTORY: a fresh root generator per
    # host, with the board bound in so trees can read all hosts' results.
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
    board: Blackboard,
) -> bool:
    """FACADE: a single entry point hiding logging setup and orchestration
    (cf. execute()/endpoint_execute()).

    Returns True if the run completed successfully, False if a Callback raised
    an exception to abort the run.  If the exception is a ``CallbackAbortError``
    (carrying the host name and callback path), structured context is logged
    together with all blackboard entries for the failing host.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    try:
        await process_inventory(inventory, root_obj_factory, board)
        return True
    except CallbackAbortError as e:
        # A Callback raised to abort the run.  Log the structured context
        # (host name, callback path) and all blackboard entries for the
        # failing host so the user can diagnose the issue.
        logging.error(
            f"Execution aborted by callback '{e.callback_path}' "
            f"on host '{e.host_name}': {e.message}"
        )
        # Log all blackboard results for the failing host.
        host_results = {
            path: resp
            for path, by_host in board.results.items()
            if e.host_name in by_host
            for resp in [by_host[e.host_name]]
        }
        if host_results:
            logging.error(
                f"Blackboard entries for host '{e.host_name}':"
            )
            for path, resp in sorted(host_results.items()):
                logging.error(f"  {path}: {resp}")
        return False
    except Exception as e:
        # An unexpected exception (not a Callback abort).  Log with full
        # traceback so the user can diagnose the issue.
        logging.error(f"Execution aborted: {e}", exc_info=True)
        return False
