# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import logging
import asyncssh
import asyncio
import inspect
from asyncssh import SSHCompletedProcess
from reemote.core.command import Command, ConnectionType
from typing import Any, AsyncGenerator, List, Tuple, Dict, Callable
from reemote.core.response import Response  # Changed import
from reemote.core.config import Config
from reemote.core.response import ssh_completed_process_to_dict
from reemote.inventory import Inventory


async def pass_through_command(command: Command) -> dict[str, str | None | Any] | None:
    if not command.group or command.group in command.inventory_item.groups:
        logging.info(f"{command.call}")
        try:
            result = {
                "host": command.inventory_item.connection.host,
                "value": command.value,
                "call": command.call,
                "changed": command.changed,
                "error": command.error,
            }

            logging.info(f"{result}")
            return result
        except Exception as e:
            logging.error(f"{e} {command}", exc_info=True)
            raise
    return None


async def run_command_on_local(command: Command) -> dict[str, str | None | Any] | None:
    if not command.group or command.group in command.inventory_item.groups:
        logging.info(f"{command.call}")
        try:
            result = {
                "host": command.inventory_item.connection.host,
                "value": await command.callback(command),
                "call": command.call,
                "changed": command.changed,
                "error": command.error,
            }
            logging.info(f"{result}")
            return result
        except Exception as e:
            logging.error(f"{e} {command}", exc_info=True)
            raise
    return None


async def run_command_on_host(
    command: Command,
) -> dict[str, str | None | bool | Any] | None:
    cp = SSHCompletedProcess()
    print(command.inventory_item)
    if not command.group or command.group in command.inventory_item.groups:
        logging.info(f"{command.call}")
        try:
            if command.get_pty:
                conn = await asyncssh.connect(
                    **command.inventory_item.connection.to_json_serializable(),
                    term_type="xterm",
                )
            else:
                conn = await asyncssh.connect(
                    **command.inventory_item.connection.to_json_serializable()
                )
            async with conn as conn:
                if command.sudo:
                    if command.inventory_item.host_vars.get("sudo_password") is None:
                        full_command = f"sudo {command.command}"
                    else:
                        full_command = f"echo {command.inventory_item.host_vars['sudo_password']} | sudo -S {command.command}"
                    cp = await conn.run(
                        full_command, check=False
                    )  # true -> check if command was successful, exception if not
                elif command.su:
                    full_command = f"su {command.inventory_item.host_vars['su_user']} -c '{command.command}'"
                    if command.inventory_item.host_vars["su_user"] == "root":
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
                                    f"{command.inventory_item.host_vars['su_password']}\n"
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
                                f"{command.inventory_item.host_vars['su_password']}\n"
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
        result = {
            "host": command.inventory_item.connection.host,
            "value": ssh_completed_process_to_dict(cp),
            "call": command.call,
            "changed": command.changed,
            "error": command.error,
        }
        logging.info(f"{result}")
        return result
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

    try:
        command = await gen.__anext__()
    except StopAsyncIteration:
        # Generator completed immediately (no commands to execute)
        return responses

    while True:
        try:
            if isinstance(command, Command):
                command.inventory_item = inventory_item

                if command.type == ConnectionType.LOCAL:
                    result = await run_command_on_local(command)
                elif command.type == ConnectionType.REMOTE:
                    result = await run_command_on_host(command)
                elif command.type == ConnectionType.PASSTHROUGH:
                    result = await pass_through_command(command)
                else:
                    raise ValueError(f"Unsupported connection type: {command.type}")

                responses.append(result)

                # Send result back and get next command
                command = await gen.asend(result)

            else:
                raise TypeError(
                    f"Unsupported type from async generator: {type(command)}"
                )

        except StopAsyncIteration:
            # Async generator is done
            break

    return responses


async def process_inventory(
    inventory: dict,
    root_obj_factory: Callable[[], Any],
) -> List[Response]:
    # Run all hosts in parallel
    tasks: List[asyncio.Task[List[Response]]] = []

    for item in inventory["hosts"]:
        task = asyncio.create_task(process_host(item, root_obj_factory))
        tasks.append(task)

    # Wait for all hosts to complete

    from typing import List

    # Wait for all hosts to complete
    all_responses: List[Any] = await asyncio.gather(*tasks)

    # Recursively flatten the nested lists and filter out None objects
    def recursive_flatten_and_filter(data):
        if isinstance(data, list):  # If it's a list, recurse into its elements
            for item in data:
                yield from recursive_flatten_and_filter(item)
        elif data is not None:  # If it's not None, yield it
            yield data

    # Flatten and filter the responses
    flattened_responses = list(recursive_flatten_and_filter(all_responses))

    # Extract the last item for each host
    unique_hosts = set(item["host"] for item in flattened_responses)  # Get unique hosts
    response = [
        next(item for item in reversed(flattened_responses) if item["host"] == host)
        for host in unique_hosts
    ]

    return response


async def execute(
    root_obj_factory: Callable[[], Any],
    inventory: Inventory,
) -> List[Response]:
    return await process_inventory(inventory.to_json_serializable(), root_obj_factory)


async def endpoint_execute(
    root_obj_factory: Callable[[], Any],
) -> List[Response]:
    config = Config()

    # Inline the reemote_logging logic here
    filepath = config.get_logging()

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG,
        filename=filepath,
        filemode="w",  # Overwrite the file each time
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Create a named logger "reemote"
    logger = logging.getLogger("reemote")
    logger.setLevel(logging.DEBUG)  # Set desired log level for your logger

    # Suppress asyncssh logs by setting its log level to WARNING or higher
    logging.getLogger("asyncssh").setLevel(logging.WARNING)

    return await process_inventory(config.get_inventory(), root_obj_factory)
