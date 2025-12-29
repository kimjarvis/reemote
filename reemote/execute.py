# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import logging
import sys
import asyncssh
import asyncio
import inspect
from asyncssh import SSHCompletedProcess
from reemote.command import Command, ConnectionType
from typing import Any, AsyncGenerator, List, Tuple, Dict, Callable
from reemote.response import Response  # Changed import
from reemote.config import Config
from reemote.logging import reemote_logging
from reemote.response import ssh_completed_process_to_dict
from reemote.inventory import get_inventory_item, Inventory
import json

async def pass_through_command(command: Command) -> dict[str, str | None | Any] | None:
    if command.group in command.global_info["groups"]:
        try:
            return {
                "host": command.host_info.get("host"),
                "value": command.value,
                "call": command.call,
                "changed": command.changed,
                "error": command.error,
            }
        except Exception as e:
            logging.error(f"{e} {command}", exc_info=True)
            raise
    return None


async def run_command_on_local(command: Command) -> dict[str, str | None | Any] | None:
    if command.group in command.global_info["groups"]:
        logging.info(f"{command}")
        r = {
            "host": command.host_info.get("host"),
            "value": await command.callback(
                command.host_info,
                command.global_info,
                command,
                SSHCompletedProcess(),
                command.caller),
            "call": command.call,
            "changed": command.changed,
            "error": command.error,
            }
        logging.info(f"{r}")
        return r
    return None

async def run_command_on_host(command: Command) -> Response:
    cp = SSHCompletedProcess()
    logging.info(f"{command}")

    if command.group in command.global_info["groups"]:
        try:
            if command.get_pty:
                conn = await asyncssh.connect(**command.host_info, term_type="xterm")
            else:
                conn = await asyncssh.connect(**command.host_info)
            async with conn as conn:
                if command.sudo:
                    if command.global_info.get("sudo_password") is None:
                        full_command = f"sudo {command.command}"
                    else:
                        full_command = f"echo {command.global_info['sudo_password']} | sudo -S {command.command}"
                    cp = await conn.run(full_command, check=False)
                elif command.su:
                    full_command = (
                        f"su {command.global_info['su_user']} -c '{command.command}'"
                    )
                    if command.global_info["su_user"] == "root":
                        async with conn.create_process(
                            full_command,
                            term_type="xterm",
                            stdin=asyncssh.PIPE,
                            stdout=asyncssh.PIPE,
                            stderr=asyncssh.PIPE,
                        ) as process:
                            try:
                                await process.stdout.readuntil("Password:")
                                process.stdin.write(
                                    f"{command.global_info['su_password']}\n"
                                )
                            except asyncio.TimeoutError:
                                pass
                            stdout, stderr = await process.communicate()
                    else:
                        async with conn.create_process(
                            full_command,
                            term_type="xterm",
                            stdin=asyncssh.PIPE,
                            stdout=asyncssh.PIPE,
                            stderr=asyncssh.PIPE,
                        ) as process:
                            await process.stdout.readuntil("Password:")
                            process.stdin.write(
                                f"{command.global_info['su_password']}\n"
                            )
                            stdout, stderr = await process.communicate()

                    cp = SSHCompletedProcess(
                        command=full_command,
                        exit_status=process.exit_status,
                        returncode=process.returncode,
                        stdout=stdout,
                        stderr=stderr,
                    )
                else:
                    cp = await conn.run(command.command, check=False)
        except (asyncssh.ProcessError, OSError, asyncssh.Error) as e:
            logging.error(f"{e} {command}", exc_info=True)
            raise
        r = {
            "value": ssh_completed_process_to_dict(cp),
            "host": command.host_info.get("host"),
            "call": command.call,
            "changed": command.changed
        }
        logging.info(f"{r}")
        return r
    return None


async def pre_order_generator_async(
    node: object,
) -> AsyncGenerator[Command | Response, Response | None]:
    """
    Async version of pre-order generator traversal.
    Handles async generators and async execute() methods.
    """
    # Stack stores tuples of (node, async_generator, send_value)
    stack = []

    # Start with the root node
    if hasattr(node, "execute") and callable(node.execute):
        # Check if execute is an async generator function
        if inspect.isasyncgenfunction(node.execute):
            gen = node.execute()
            stack.append((node, gen, None))
        else:
            # It's a regular async function (coroutine)
            # We'll execute it and return its result
            result = await node.execute()
            # Yield a special marker to indicate completion
            yield None
            # The caller should recognize None as a sentinel and stop
            return
    else:
        raise TypeError(f"Node must have an execute() method: {type(node)}")

    while stack:
        current_node, generator, send_value = stack[-1]

        try:
            if send_value is None:
                # First time or after pushing new generator
                value = await generator.__anext__()
            else:
                # Send previous result
                value = await generator.asend(send_value)

            # Process the yielded value
            if isinstance(value, Command):
                # Yield the command for execution
                result = yield value
                # Store result to send back
                stack[-1] = (current_node, generator, result)

            elif hasattr(value, "execute") and callable(value.execute):
                # Nested operation (like Child, Shell, or Return)
                # Execute it and push onto stack
                nested_execute = value.execute()

                # Check if it's an async generator
                if inspect.isasyncgenfunction(value.execute):
                    nested_gen = nested_execute
                    stack.append((value, nested_gen, None))
                else:
                    # It's a coroutine - execute it immediately
                    result = await nested_execute
                    # Send result back to parent
                    stack[-1] = (current_node, generator, result)

            elif isinstance(value, Response):
                # Pass through Response objects
                result = yield value
                stack[-1] = (current_node, generator, result)

            else:
                # Unsupported type
                raise TypeError(
                    f"Unsupported yield type from async generator: {type(value)}"
                )

        except StopAsyncIteration as e:
            # Async generator is done
            # Get the return value if any
            return_value = e.value if hasattr(e, "value") else send_value

            stack.pop()

            # If there's a parent generator, send back the return value
            if stack:
                stack[-1] = (stack[-1][0], stack[-1][1], return_value)

        except Exception as e:
            logging.error(f"{e}", exc_info=True)
            raise



async def process_host(
    inventory_item: Tuple[Dict[str, Any], Dict[str, Any]],
    obj_factory: Callable[[], Any],
) -> List[Response]:
    responses: List[Response] = []

    # Create a new instance for this host using the factory
    host_instance = obj_factory()

    # Create async pre-order generator
    gen = pre_order_generator_async(host_instance)

    # Start the generator
    operation = None
    try:
        operation = await gen.__anext__()
    except StopAsyncIteration:
        # Generator completed immediately (no commands to execute)
        return responses

    while True:
        try:
            if isinstance(operation, Command):
                # Set inventory info
                # operation.host_info, operation.global_info = inventory_item
                operation.host_info, operation.global_info = get_inventory_item(inventory_item)

                if operation.type == ConnectionType.LOCAL:
                    result = await run_command_on_local(operation)
                elif operation.type == ConnectionType.REMOTE:
                    result = await run_command_on_host(operation)
                elif operation.type == ConnectionType.PASSTHROUGH:
                    result = await pass_through_command(operation)
                else:
                    raise ValueError(
                        f"Unsupported connection type: {operation.type}"
                    )

                responses.append(result)

                # Send result back and get next operation
                operation = await gen.asend(result)

            elif isinstance(operation, Response):
                responses.append(operation)
                result = operation
                operation = await gen.asend(result)

            else:
                raise TypeError(
                    f"Unsupported type from async generator: {type(operation)}"
                )

        except StopAsyncIteration:
            # Async generator is done
            break

    return responses

async def execute(
    root_obj_factory: Callable[[], Any],
    inventory: Inventory,
    logfile: str,
) -> List[Response]:  # Changed return type

    reemote_logging(logfile)

    # Run all hosts in parallel
    tasks: List[asyncio.Task[List[Response]]] = []  # Changed type

    for item in inventory.to_json_serializable()["hosts"]:
        task = asyncio.create_task(process_host(item, root_obj_factory))
        tasks.append(task)

    # Wait for all hosts to complete
    all_responses: List[List[Response]] = await asyncio.gather(*tasks)  # Changed type

    # Flatten the list of lists
    response: List[Response] = []  # Changed type
    for host_responses in all_responses:
        response.extend(host_responses)

    return response

async def endpoint_execute(
    root_obj_factory: Callable[[], Any],
) -> List[Response]:  # Changed return type

    config = Config()
    reemote_logging()

    # Run all hosts in parallel
    tasks: List[asyncio.Task[List[Response]]] = []  # Changed type

    for item in config.get_inventory()["hosts"]:
        task = asyncio.create_task(process_host(item, root_obj_factory))
        tasks.append(task)

    # Wait for all hosts to complete
    all_responses: List[List[Response]] = await asyncio.gather(*tasks)  # Changed type

    # Flatten the list of lists
    response: List[Response] = []  # Changed type
    for host_responses in all_responses:
        response.extend(host_responses)

    return response
