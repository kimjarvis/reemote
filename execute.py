# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import asyncssh
import asyncio
from asyncssh import SSHCompletedProcess
from command import Command
from typing import Iterable, Any, AsyncGenerator, List, Tuple, Dict, Callable
from response import Response  # Changed import
import logging

async def run_command_on_local(operation: Command) -> Response:  # Changed return type
    logging.debug("run_command_on_local entry")
    host_info = operation.host_info
    global_info = operation.global_info
    command = operation.command
    cp = SSHCompletedProcess()
    executed = False
    caller = operation.caller

    try:
        result = await operation.callback(host_info, global_info, command, cp, caller)

        # Set successful return codes for local operations
        cp.exit_status = 0
        cp.returncode = 0
        cp.stdout = result
        executed = True

        return Response(
            cp=cp,
            host=host_info.get("host"),
            op=operation,
            executed=executed
        )
    except Exception as e:
        raw_error = str(e)
        cp = SSHCompletedProcess()
        cp.exit_status = 1
        cp.returncode = 1
        cp.stderr = raw_error  # Also set stderr for consistency
        return Response(
            cp=cp,
            error=raw_error,
            host=host_info.get("host"),
            op=operation,
            executed=True
        )


async def run_command_on_host(operation: Command) -> Response:  # Changed return type
    host_info = operation.host_info
    global_info = operation.global_info
    command = operation.command
    cp = SSHCompletedProcess()
    executed = False

    try:
        if operation.get_pty:
            conn = await asyncssh.connect(**host_info, term_type='xterm')
        else:
            conn = await asyncssh.connect(**host_info)
        async with conn as conn:
            if not operation.guard:
                pass
            else:
                executed = True
                if operation.sudo:
                    if global_info.get('sudo_password') is None:
                        full_command = f"sudo {command}"
                    else:
                        full_command = f"echo {global_info['sudo_password']} | sudo -S {command}"
                    cp = await conn.run(full_command, check=False)
                elif operation.su:
                    full_command = f"su {global_info['su_user']} -c '{command}'"
                    if global_info["su_user"] == "root":
                        async with conn.create_process(full_command,
                                                       term_type='xterm',
                                                       stdin=asyncssh.PIPE, stdout=asyncssh.PIPE,
                                                       stderr=asyncssh.PIPE) as process:
                            try:
                                output = await process.stdout.readuntil('Password:')
                                process.stdin.write(f'{global_info["su_password"]}\n')
                            except asyncio.TimeoutError:
                                pass
                            stdout, stderr = await process.communicate()
                    else:
                        async with conn.create_process(full_command,
                                                       term_type='xterm',
                                                       stdin=asyncssh.PIPE, stdout=asyncssh.PIPE,
                                                       stderr=asyncssh.PIPE) as process:
                            output = await process.stdout.readuntil('Password:')
                            process.stdin.write(f'{global_info["su_password"]}\n')
                            stdout, stderr = await process.communicate()

                    cp = SSHCompletedProcess(
                        command=full_command,
                        exit_status=process.exit_status,
                        returncode=process.returncode,
                        stdout=stdout,
                        stderr=stderr
                    )
                else:
                    cp = await conn.run(command, check=False)
    except asyncssh.ProcessError as exc:
        raw_error = str(exc)
        cp = SSHCompletedProcess()
        cp.exit_status = exc.exit_status if hasattr(exc, 'exit_status') else 1
        cp.returncode = exc.exit_status if hasattr(exc, 'exit_status') else 1
        error_msg = f"Process on host {host_info.get('host')} exited with status {exc.exit_status}"
        cp.stderr = raw_error
        return Response(  # Changed to UnifiedResult
            cp=cp,
            error=error_msg,
            host=host_info.get("host"),
            op=operation,
            executed=executed
        )

    except (OSError, asyncssh.Error) as e:
        raw_error = str(e)
        cp = SSHCompletedProcess()
        cp.exit_status = 1
        cp.returncode = 1
        return Response(  # Changed to UnifiedResult
            cp=cp,
            error=raw_error,
            host=host_info.get("host"),
            op=operation,
            executed=executed
        )

    return Response(  # Changed to UnifiedResult
        cp=cp,
        host=host_info.get("host"),
        op=operation,
        executed=executed
    )


async def pre_order_generator_async(node: object) -> AsyncGenerator[Command | Response, Response | None]:  # Changed type hints
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
                # Found a nested operation with its own execute()
                # Push it onto the stack. Do NOT send any value yet.
                nested_gen = value.execute()
                stack.append((value, nested_gen, None))
                # Do not yield or send any value to a just-started async generator.
                # It will be primed on the next loop iteration via __anext__.

            elif isinstance(value, Response):  # Changed type check
                # Pass through UnifiedResult objects
                print("trace 00")
                result = yield value
                stack[-1] = (current_node, generator, result)

            else:
                raise TypeError(f"Unsupported yield type from async generator: {type(value)}")

        except StopAsyncIteration as e:
            # Async generator is done
            # Capture the last value we attempted to send into this generator
            last_sent = stack[-1][2] if stack else None
            stack.pop()
            # If there's a parent generator, send back a meaningful value
            if stack:
                # Prefer an explicit return value from the generator if provided;
                # otherwise, propagate the last result we sent into the child.
                explicit = e.value if hasattr(e, 'value') else None
                return_value = explicit if explicit is not None else last_sent
                stack[-1] = (stack[-1][0], stack[-1][1], return_value)

        except Exception as e:
            print(f"Error in async node execution: {e}")
            print(f"Current node: {current_node}")
            print(f"Node type: {type(current_node)}")

            # Handle errors - yield a UnifiedResult object
            error_msg = f"Error in node execution: {e}"
            result = yield Response(error=error_msg)  # Changed to UnifiedResult
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
            print(f"Error processing host {inventory_item[0]}: {e}")
            import traceback
            traceback.print_exc()

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
