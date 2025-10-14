# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import asyncssh
import asyncio
from asyncssh import SSHCompletedProcess
from reemote.command import Command
from reemote.result import Result


async def run_command_on_local(operation):
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

        return Result(cp=cp, host=host_info.get("host"), op=operation, executed=executed)
    except Exception as e:
        raw_error = str(e)
        # Always set proper exit codes for failures, regardless of composite flag
        cp = SSHCompletedProcess()
        cp.exit_status = 1
        cp.returncode = 1

        if operation.composite:
            # Composite operations get raw error only
            return Result(cp=cp, error=raw_error, host=host_info.get("host"), op=operation, executed=True)
        else:
            # Non-composite operations get error field only
            cp.stderr = raw_error  # Also set stderr for consistency
            return Result(cp=cp, error=raw_error, host=host_info.get("host"), op=operation, executed=True)


async def run_command_on_host(operation):
    host_info = operation.host_info
    global_info = operation.global_info
    command = operation.command
    cp = SSHCompletedProcess()
    executed = False

    try:
        async with asyncssh.connect(**host_info) as conn:
            if operation.composite:
                # Composite operations (like Directory) get raw error messages
                pass
            else:
                if not operation.guard:
                    pass
                else:
                    executed = True
                    if operation.sudo:
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

        if operation.composite:
            # Composite operations get raw error only
            return Result(cp=cp, error=raw_error, host=host_info.get("host"), op=operation, executed=executed)
        else:
            # Non-composite operations get formatted error
            error_msg = f"Process on host {host_info.get('host')} exited with status {exc.exit_status}"
            cp.stderr = raw_error
            return Result(cp=cp, error=error_msg, host=host_info.get("host"), op=operation, executed=executed)

    except (OSError, asyncssh.Error) as e:
        raw_error = str(e)
        cp = SSHCompletedProcess()
        cp.exit_status = 1
        cp.returncode = 1

        if operation.composite:
            # Composite operations (like Directory) get raw error only
            return Result(cp=cp, error=raw_error, host=host_info.get("host"), op=operation, executed=executed)
        else:
            # Non-composite operations (like Isdir, Mkdir) get error field only
            return Result(cp=cp, error=raw_error, host=host_info.get("host"), op=operation, executed=executed)

    return Result(cp=cp, host=host_info.get("host"), op=operation, executed=executed)

def pre_order_generator(node):
    """
    Enhanced generator function with better error handling and string wrapping.
    """
    stack = [(node, iter(node.execute()))]
    result = None

    while stack:
        current_node, iterator = stack[-1]
        try:
            value = iterator.send(result) if result is not None else next(iterator)
            result = None

            if isinstance(value, Command):
                result = yield value
            elif hasattr(value, 'execute') and callable(value.execute):
                # If it's a node with execute method, push to stack
                stack.append((value, iter(value.execute())))
            else:
                raise TypeError(f"Unsupported yield type: {type(value)}")

        except StopIteration:
            stack.pop()
        except Exception as e:
            print(f"Error in node execution: {e}")
            print(f"Current node: {current_node}")
            print(f"Node type: {type(current_node)}")
            # Handle errors in node execution - yield a Result object instead of printing
            error_msg = f"Error in node execution: {e}"
            result = yield Result(error=error_msg)
            stack.pop()


async def execute(inventory, obj):
    """
    Execute a deployment operation across multiple hosts defined in the inventory.

    This function traverses the operation tree using pre-order traversal, executing
    each command on the specified hosts. It supports both local and remote command
    execution with various authentication methods (sudo, su).

    Args:
        inventory (list): A list of tuples containing host information and global
                         information for each target host. Each tuple should contain
                         (host_info: Dict[str, str], global_info: Dict[str, str]).
        obj: The root deployment object that has an execute() method which yields
             Command objects to be executed.

    Returns:
        list: A list of Result objects containing the execution results for each
              command executed across all hosts.

    The function handles two types of objects during traversal:
    - Command: Executable commands that will be run on hosts
    - Result: Error results that are collected directly

    For each Command, the function:
    1. Sets host_info and global_info from the inventory
    2. Executes the command either locally (if operation.local is True) or remotely
    3. Collects the result in the responses list
    """
    operations = []
    responses = []

    roots = []
    inventory_items = []
    for inventory_item in inventory:
        roots.append(obj)
        inventory_items.append(inventory_item)  # Store the inventory item

    # Create generators for step-wise traversal of each tree
    generators = [pre_order_generator(root) for root in roots]
    results = {gen: None for gen in generators}  # Initialize results as None

    done = False
    while not done:
        all_done = True

        for gen, inventory_item in zip(generators, inventory_items):
            try:
                # Get the next operation or result from the generator
                yielded_object = gen.send(results[gen])

                # Check if the yielded object is an Operation
                if isinstance(yielded_object, Command):
                    operation = yielded_object
                    operation.host_info, operation.global_info = inventory_item

                    if operation.local:
                        results[gen] = await run_command_on_local(operation)
                    else:
                        results[gen] = await run_command_on_host(operation)

                    operations.append(operation)
                    responses.append(results[gen])
                    all_done = False

                elif isinstance(yielded_object, Result):
                    # Handle the Result object (e.g., log the error or skip)
                    print(f"Received Result object: {yielded_object}")
                    responses.append(yielded_object)
                    results[gen] = yielded_object  # Pass the result back to the generator

                else:
                    raise TypeError(f"Unsupported type yielded by generator: {type(yielded_object)}")

            except StopIteration:
                pass

        # If all generators are done, exit the loop
        done = all_done

    return responses
