# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import logging
import asyncssh
import asyncio
from asyncssh import SSHCompletedProcess
from command import Command
from typing import Iterable, Any, AsyncGenerator, List, Tuple, Dict, Callable
from response import Response  # Changed import

async def run_command_on_local(command: Command) -> Response:
    cp = SSHCompletedProcess()

    if command.group not in command.global_info["groups"]:
        return Response(
            cp=cp,
            host=command.host_info.get("host"),
            op=command,
            executed=False
        )
    else:
        logging.debug(
            f"run_command_on_local begin {command}"
        )

        try:
            result = await command.callback(command.host_info, command.global_info, command, cp, command.caller)

            print(type(result))
            print(result)

            # Set successful return codes for local operations
            cp.exit_status = 0
            cp.returncode = 0

            response = Response(
                cp=cp,
                host=command.host_info.get("host"),
                op=command,
                output=result
            )
            logging.debug(
                f"run_command_on_local end {response}"
            )
            return response
        except Exception as e:
            cp = SSHCompletedProcess()
            logging.error(
                f"{e} {command} {cp}",
                exc_info=True
            )
            cp.exit_status = 1
            cp.returncode = 1
            return Response(
                cp=cp,
                error=str(e),
                host=command.host_info.get("host"),
                op=command,
            )

async def run_command_on_host(command: Command) -> Response:
    cp = SSHCompletedProcess()

    if command.group not in command.global_info["groups"]:
        return Response(
            cp=cp,
            host=command.host_info.get("host"),
            op=command,
            executed=False
        )
    else:

        logging.debug(
            f"run_command_on_host begin {command.host_info}, {command.global_info}, {command.command}"
        )
        try:
            if command.get_pty:
                conn = await asyncssh.connect(**command.host_info, term_type='xterm')
            else:
                conn = await asyncssh.connect(**command.host_info)
            async with conn as conn:
                if command.sudo:
                    if command.global_info.get('sudo_password') is None:
                        full_command = f"sudo {command.command}"
                    else:
                        full_command = f"echo {command.global_info['sudo_password']} | sudo -S {command.command}"
                    cp = await conn.run(full_command, check=False)
                elif command.su:
                    full_command = f"su {command.global_info['su_user']} -c '{command.command}'"
                    if command.global_info["su_user"] == "root":
                        async with conn.create_process(full_command,
                                                       term_type='xterm',
                                                       stdin=asyncssh.PIPE, stdout=asyncssh.PIPE,
                                                       stderr=asyncssh.PIPE) as process:
                            try:
                                output = await process.stdout.readuntil('Password:')
                                process.stdin.write(f'{command.global_info["su_password"]}\n')
                            except asyncio.TimeoutError:
                                pass
                            stdout, stderr = await process.communicate()
                    else:
                        async with conn.create_process(full_command,
                                                       term_type='xterm',
                                                       stdin=asyncssh.PIPE, stdout=asyncssh.PIPE,
                                                       stderr=asyncssh.PIPE) as process:
                            output = await process.stdout.readuntil('Password:')
                            process.stdin.write(f'{command.global_info["su_password"]}\n')
                            stdout, stderr = await process.communicate()

                    cp = SSHCompletedProcess(
                        command=full_command,
                        exit_status=process.exit_status,
                        returncode=process.returncode,
                        stdout=stdout,
                        stderr=stderr
                    )
                else:
                    cp = await conn.run(command.command, check=False)
        except asyncssh.ProcessError as exc:
            cp = SSHCompletedProcess()
            logging.error(
                f"{e} {command} {cp}",
                exc_info=True
            )
            cp.exit_status = exc.exit_status if hasattr(exc, 'exit_status') else 1
            cp.returncode = exc.exit_status if hasattr(exc, 'exit_status') else 1
            error_msg = f"Process on host {command.host_info.get('host')} exited with status {exc.exit_status}"
            cp.stderr = raw_error
            return Response(
                cp=cp,
                error=str(e),
                host=command.host_info.get("host"),
                op=command,
            )

        except (OSError, asyncssh.Error) as e:
            cp = SSHCompletedProcess()
            logging.error(
                f"{e} {cp}",
                exc_info=True
            )
            cp.exit_status = 1
            cp.returncode = 1
            return Response(
                cp=cp,
                error=str(e),
                host=command.host_info.get("host"),
                op=command,
            )


        response = Response(
            cp=cp,
            host=command.host_info.get("host"),
            op=command,
        )
        logging.debug(
            f"run_command_on_host end {response}"
        )
        return response

async def pre_order_generator_async(node: object) -> AsyncGenerator[Command | Response, Response | None]:
    """
    Async version of pre-order generator traversal.
    Handles async generators and async execute() methods.
    """
    # Stack stores tuples of (node, async_generator, send_value)
    stack = []

    # Start with the root node
    if hasattr(node, 'execute') and callable(node.execute):
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

            elif hasattr(value, 'execute') and callable(value.execute):
                # Special handling for Shell-like objects
                # They yield Commands but we want results to go to parent
                from commands.server import Shell
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
                raise TypeError(f"Unsupported yield type from async generator: {type(value)}")

        except StopAsyncIteration as e:
            # Async generator is done
            completed_node = stack[-1][0] if stack else None

            # Get the collected results from the completed node
            collected_results = getattr(completed_node, '_yielded_results', None)

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

async def execute(inventory: Iterable[Tuple[Dict[str, Any], Dict[str, Any]]], root_obj_factory: Callable[[], Any]) -> List[Response]:  # Changed return type
    async def process_host(inventory_item: Tuple[Dict[str, Any], Dict[str, Any]], root_obj_factory: Callable[[], Any]) -> List[Response]:  # Changed return type
        responses: List[Response] = []  # Changed type

        # Create a new instance for this host using the factory
        host_instance = root_obj_factory()

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
                        raise TypeError(f"Unsupported type from async generator: {type(operation)}")

                except StopAsyncIteration:
                    # Async generator is done
                    break

        except Exception as e:
            # Handle any errors for this host
            logging.error(f"Error processing host {inventory_item[0]}: {e}", exc_info=True)

        return responses

    # Run all hosts in parallel
    tasks: List[asyncio.Task[List[Response]]] = []  # Changed type
    for inventory_item in inventory:
        task = asyncio.create_task(process_host(inventory_item, root_obj_factory))
        tasks.append(task)

    # Wait for all hosts to complete
    all_responses: List[List[Response]] = await asyncio.gather(*tasks)  # Changed type

    # Flatten the list of lists
    responses: List[Response] = []  # Changed type
    for host_responses in all_responses:
        responses.extend(host_responses)

    return responses