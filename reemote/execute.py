# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import logging
import sys
import asyncssh
import asyncio
from asyncssh import SSHCompletedProcess
from reemote.command import Command
from typing import Any, AsyncGenerator, List, Tuple, Dict, Callable
from reemote.response import Response  # Changed import
from reemote.config import Config
from reemote.logging import reemote_logging

async def run_command_on_local(command: Command) -> Response:
    # logging.info(f"run on local - {command}")

    if command.group not in command.global_info["groups"]:
        return Response.from_command(
            command, host=command.host_info.get("host"), executed=False
        )
    else:
        try:
            return Response.from_command(
                command,
                host=command.host_info.get("host"),
                output=await command.callback(
                    command.host_info,
                    command.global_info,
                    command,
                    SSHCompletedProcess(),
                    command.caller,
                ),
            )
        except Exception as e:
            logging.error(f"{e} {command}", exc_info=True)
            sys.exit(1)


async def run_command_on_host(command: Command) -> Response:
    cp = SSHCompletedProcess()
    # logging.info(f"run on host - {command}")

    if command.group not in command.global_info["groups"]:
        return Response.from_command(
            command, host=command.host_info.get("host"), executed=False
        )
    else:
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
            sys.exit(1)
        return Response(
            cp=cp,
            host=command.host_info.get("host"),
            op=command,
        )


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
        gen = node.execute()
        # For async generators, we can't call next() immediately
        stack.append((node, gen, None))
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
                # Special handling for Shell-like objects
                # They yield Commands, but we want results to go to parent
                from reemote.commands.server import Shell

                if isinstance(value, Shell):
                    # Get the Command from Shell
                    shell_gen = value.execute()
                    try:
                        # Get Command from Shell
                        command = await shell_gen.__anext__()

                        # Execute the Command
                        result = yield command

                        # Shell generator should be done now
                        try:
                            # Try to send result to Shell (it might not accept)
                            await shell_gen.asend(result)
                        except StopAsyncIteration:
                            # Shell is done
                            pass

                        # Send result DIRECTLY back to parent (Hello)
                        stack[-1] = (current_node, generator, result)

                    except StopAsyncIteration:
                        # Shell had no commands
                        stack[-1] = (current_node, generator, None)
                    finally:
                        await shell_gen.aclose()

                else:
                    # Real nested operation (like Hello)
                    # Push it onto the stack
                    nested_gen = value.execute()
                    stack.append((value, nested_gen, None))

            elif isinstance(value, Response):
                # Pass through Response objects
                result = yield value
                stack[-1] = (current_node, generator, result)

            else:
                raise TypeError(
                    f"Unsupported yield type from async generator: {type(value)}"
                )

        except StopAsyncIteration:
            # Async generator is done
            completed_node = stack[-1][0] if stack else None

            # Get the collected results from the completed node
            collected_results = getattr(completed_node, "_yielded_results", None)

            stack.pop()

            # If there's a parent generator, send back a meaningful value
            if stack:
                # Check if we have collected results
                if collected_results is not None:
                    # Return the collected results
                    return_value = collected_results
                else:
                    # No collected results, use last sent value
                    return_value = send_value

                stack[-1] = (stack[-1][0], stack[-1][1], return_value)

        except Exception as e:
            # Handle errors
            error_msg = f"Error in node execution: {e}"
            result = yield Response(error=error_msg)
            stack.pop()

            # Send error to parent if exists
            if stack:
                stack[-1] = (stack[-1][0], stack[-1][1], result)


async def execute(
    root_obj_factory: Callable[[], Any],
) -> List[Response]:  # Changed return type
    async def process_host(
        inventory_item: Tuple[Dict[str, Any], Dict[str, Any]],
        obj_factory: Callable[[], Any],
    ) -> List[Response]:  # Changed return type
        responses: List[Response] = []  # Changed type

        # Create a new instance for this host using the factory
        host_instance = obj_factory()

        # Create async pre-order generator
        gen = pre_order_generator_async(host_instance)

        try:
            # Prime the async generator - get first value
            operation = await gen.__anext__()

            while True:
                try:
                    if isinstance(operation, Command):
                        # Set inventory info
                        operation.host_info, operation.global_info = inventory_item

                        # Execute the command
                        if operation.local:
                            result = await run_command_on_local(operation)
                        else:
                            result = await run_command_on_host(operation)

                        responses.append(result)

                        # Send result back and get next operation
                        operation = await gen.asend(result)

                    elif isinstance(operation, Response):  # Changed type check
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

        except Exception as e:
            # Handle any errors for this host
            logging.error(
                f"Error processing host {inventory_item}: {e}", exc_info=True
            )

        return responses

    config = Config()
    reemote_logging()

    # Run all hosts in parallel
    tasks: List[asyncio.Task[List[Response]]] = []  # Changed type
    for item in config.get_inventory_data():
        task = asyncio.create_task(process_host(item, root_obj_factory))
        tasks.append(task)

    # Wait for all hosts to complete
    all_responses: List[List[Response]] = await asyncio.gather(*tasks)  # Changed type

    # Flatten the list of lists
    response: List[Response] = []  # Changed type
    for host_responses in all_responses:
        response.extend(host_responses)

    return response
