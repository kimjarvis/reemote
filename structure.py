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
3. Strategy        -- ``run_callback`` / ``run_operation`` / ``run_passthrough``
                      are selected via ``context.type``; the actual work is a
                      pluggable ``context.callback``.
4. Composite       -- command objects form a tree: the root yields child
                      nodes, children may themselves be composites.
5. Iterator / IoC  -- ``pre_order_generator_async`` externalizes tree
                      traversal as an async generator driven through the
                      bidirectional ``yield`` / ``asend`` coroutine protocol.
6. Adapter         -- ``completed_process_to_dict`` converts simulated
                      ``CompletedProcess`` objects into plain dicts.
7. Facade          -- ``execute()`` hides orchestration behind one entry point.
8. Scatter-Gather  -- ``process_inventory`` fans out one asyncio task per host
                      and fans in with ``asyncio.gather``.
9. Null Object     -- hosts not matching a group produce ``None`` instead of
                      exceptions or special-casing; filtered downstream.
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
    """Stand-in for an inventory item: just a name and group memberships."""

    name: str
    groups: Tuple[str, ...] = ()


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
    GET = auto()
    POST = auto()
    PUT = auto()


class ContextType(Enum):
    CALLBACK = auto()
    OPERATION = auto()
    PASSTHROUGH = auto()


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
    operation tree (producer) and the runners (executors).

    Mirrors ``reemote.context.Context``.
    """

    type: ContextType
    method: Method = Method.GET
    command: str = ""
    callback: Callable[["Context"], Any] | None = None
    group: str | None = None
    request_instance: Any = None
    response: Callable[..., Any] = DemoResponse
    # Populated by the driver (process_host) before execution:
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
    """Build the response, varying the fields by HTTP method (as in execute.py)."""
    match context.method:
        case Method.GET:
            return context.response(
                host=context.inventory_item.name,
                error=context.error,
                message=context.value if context.error else "",
                value=context.value if not context.error else "",
                request=context.request_instance,
            )
        case Method.POST:
            return context.response(
                host=context.inventory_item.name,
                error=context.error,
                message=context.value if context.error else "",
                request=context.request_instance,
            )
        case Method.PUT:
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
# Runners -- the interchangeable execution STRATEGIES
# ---------------------------------------------------------------------------


async def run_passthrough(context: Context) -> Any | None:
    """Strategy A: raw execution -- exceptions propagate to the caller."""
    if not group_matches(context):
        return None  # NULL OBJECT: "not applicable here" is a value, not an error
    logging.info(f"{context.inventory_item.name:<16} - passthrough")
    context.value = await context.callback(context)
    return get_result(context)


async def run_callback(context: Context) -> Any | None:
    """Strategy B: contained execution -- exceptions become error results."""
    if not group_matches(context):
        return None
    logging.info(f"{context.inventory_item.name:<16} - callback")
    try:
        context.value = await context.callback(context)
        return get_result(context)
    except Exception as e:
        context.error = True
        context.value = (
            f"{e.__class__.__name__} on host {context.inventory_item.name}: {e}"
        )
        return get_result(context)


async def run_operation(context: Context) -> Any | None:
    """Strategy C: the "remote command" runner.

    In reemote this opens an SSH connection (with sudo/su handling); here the
    execution is simulated in-process.
    """
    if not group_matches(context):
        return None
    logging.info(f"{context.inventory_item.name:<16} - {context.command}")
    await asyncio.sleep(0.05)  # simulated network latency

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
    result = get_result(context)
    if context.value["returncode"] != 0:
        raise RuntimeError(f"non-zero return code: {context.value['stderr']}")
    return result


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

    if hasattr(node, "execute") and callable(node.execute):
        if inspect.isasyncgenfunction(node.execute):
            stack.append((node, node.execute(), None))
        else:
            # Regular coroutine node: run it and signal completion.
            await node.execute()
            yield None
            return
    else:
        raise TypeError(f"Node must have an execute() method: {type(node)}")

    while stack:
        current_node, generator, send_value = stack[-1]
        try:
            if send_value is None:
                value = await generator.__anext__()
            else:
                value = await generator.asend(send_value)

            if isinstance(value, Context):
                # A unit of work: hand it to the driver, await its result.
                result = yield value
                stack[-1] = (current_node, generator, result)

            elif hasattr(value, "execute") and callable(value.execute):
                # COMPOSITE: a nested operation node -- descend into it.
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
# Composite tree nodes (the demo "operations")
# ---------------------------------------------------------------------------


class Shell:
    """Leaf OPERATION node -- stands in for reemote's shell command op."""

    def __init__(self, command: str, group: str | None = None):
        self.command = command
        self.group = group

    async def execute(self):
        result = yield Context(
            type=ContextType.OPERATION,
            method=Method.GET,
            command=self.command,
            group=self.group,
            request_instance={"command": self.command},
        )
        return result


class Callback:
    """Leaf CALLBACK node -- wraps a pluggable async function (STRATEGY
    injected from the outside).  Exceptions are contained into error results."""

    def __init__(self, func: Callable[[Context], Any], group: str | None = None):
        self.func = func
        self.group = group

    async def execute(self):
        result = yield Context(
            type=ContextType.CALLBACK,
            method=Method.POST,
            callback=self.func,
            group=self.group,
            request_instance={"callback": func_name(self.func)},
        )
        return result


class Announce:
    """Leaf PASSTHROUGH node (PUT semantics: reports ``changed``)."""

    def __init__(self, func: Callable[[Context], Any], group: str | None = None):
        self.func = func
        self.group = group

    async def execute(self):
        result = yield Context(
            type=ContextType.PASSTHROUGH,
            method=Method.PUT,
            callback=self.func,
            group=self.group,
        )
        return result


class Sequence:
    """COMPOSITE node: runs children in order, collecting their results."""

    def __init__(self, *children: Any):
        self.children = children

    async def execute(self):
        results = []
        for child in self.children:
            results.append((yield child))
        return results


def func_name(f: Callable) -> Any:
    return getattr(f, "__name__", f)


# ---------------------------------------------------------------------------
# Demo callbacks (the pluggable strategies)
# ---------------------------------------------------------------------------


def make_uppercase_callback(text: str) -> Callable[[Context], Any]:
    """Factory for a callback bound to per-request data."""

    async def callback(context: Context) -> str:
        return f"{text.upper()} from {context.inventory_item.name}"

    return callback


async def flaky_callback(context: Context) -> str:
    """Deliberately fails on hosts whose name ends in '-2' to demonstrate
    run_callback's error containment (contrast with run_passthrough)."""
    if context.inventory_item.name.endswith("-2"):
        raise RuntimeError("simulated callback failure")
    return "callback ok"


async def announce_callback(context: Context) -> str:
    context.changed = True  # PUT-style "something changed" flag
    return f"announced on {context.inventory_item.name}"


# ---------------------------------------------------------------------------
# The root command object (built by the FACTORY, once per host)
# ---------------------------------------------------------------------------


class DemoCommand:
    """Root of the composite tree -- analogous to a reemote endpoint command
    class.  A fresh instance is created per host by the factory, so no state
    is shared between hosts.
    """

    def __init__(self, greeting: str = "hello"):
        self.greeting = greeting

    async def execute(self):
        # OPERATION child, targeted at the "linux" group only.
        # On non-matching hosts run_operation returns None (NULL OBJECT).
        yield Shell("uname -s", group="linux")

        # CALLBACK child with a strategy bound to request data.
        yield Callback(make_uppercase_callback(self.greeting))

        # CALLBACK child that fails on one host -> contained error result.
        yield Callback(flaky_callback)

        # PASSTHROUGH child, restricted to webservers; PUT semantics.
        yield Announce(announce_callback, group="webservers")

        # Nested COMPOSITE: a Sequence of further operations.
        yield Sequence(
            Shell("echo step-1"),
            Shell("echo step-2"),
        )
        # Note: as in execute.py, the root's return value is discarded by the
        # driver; responses are collected per yielded Context.


# ---------------------------------------------------------------------------
# Host & inventory orchestration
# ---------------------------------------------------------------------------


async def process_host(
    host: SimulatedHost,
    obj_factory: Callable[[], Any],
) -> List[Any]:
    """Drive one host's operation tree.

    FACTORY: a fresh command instance per host.
    STRATEGY: dispatch on ``context.type`` picks the runner.
    """
    responses: List[Any] = []

    host_instance = obj_factory()
    gen = pre_order_generator_async(host_instance)

    try:
        context = await gen.__anext__()
    except StopAsyncIteration:
        return responses

    while True:
        try:
            if isinstance(context, Context):
                context.inventory_item = host
                match context.type:
                    case ContextType.CALLBACK:
                        result = await run_callback(context)
                    case ContextType.OPERATION:
                        result = await run_operation(context)
                    case ContextType.PASSTHROUGH:
                        result = await run_passthrough(context)
                    case _:
                        raise ValueError(f"Unsupported context type: {context.type}")

                responses.append(result)  # may be None (NULL OBJECT)
                context = await gen.asend(result)
            else:
                raise TypeError(
                    f"Unsupported type from async generator: {type(context)}"
                )
        except StopAsyncIteration:
            break

    return responses


async def process_inventory(
    hosts: List[SimulatedHost],
    root_obj_factory: Callable[[], Any],
) -> List[Any]:
    """SCATTER-GATHER: fan out one task per host, fan in with gather,
    flatten nested lists, drop None (NULL OBJECT filtering), and reduce to
    the last response per host -- exactly like execute.py.
    """
    if not hosts:
        return []

    # Scatter
    tasks = [asyncio.create_task(process_host(h, root_obj_factory)) for h in hosts]
    # Gather
    all_responses: List[Any] = await asyncio.gather(*tasks)

    def recursive_flatten_and_filter(data):
        if isinstance(data, list):
            for item in data:
                yield from recursive_flatten_and_filter(item)
        elif data is not None:
            yield data

    flattened_responses = list(recursive_flatten_and_filter(all_responses))

    # Keep the last response per host (ordered, for deterministic demo output).
    unique_hosts: List[str] = []
    for item in flattened_responses:
        if item.host not in unique_hosts:
            unique_hosts.append(item.host)
    return [
        next(item for item in reversed(flattened_responses) if item.host == host)
        for host in unique_hosts
    ]


# ---------------------------------------------------------------------------
# Facade & demo entry point
# ---------------------------------------------------------------------------


async def execute(
    root_obj_factory: Callable[[], Any],
    hosts: List[SimulatedHost],
) -> List[Any]:
    """FACADE: a single entry point hiding logging setup, orchestration,
    flattening and deduplication (cf. execute()/endpoint_execute())."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    return await process_inventory(hosts, root_obj_factory)


async def main() -> None:
    # A tiny simulated "inventory".
    hosts = [
        SimulatedHost("web-1", ("linux", "webservers")),
        SimulatedHost("web-2", ("linux", "webservers")),
        SimulatedHost("win-1", ("windows",)),
    ]

    # FACTORY: the caller supplies how to build the root command object.
    responses = await execute(lambda: DemoCommand(greeting="hello"), hosts)

    print("\n=== Final responses (last per host, as in reemote) ===")
    for r in responses:
        print(r)


if __name__ == "__main__":
    asyncio.run(main())
