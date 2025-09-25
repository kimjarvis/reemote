import asyncssh
import asyncio
from asyncssh import SSHCompletedProcess
from reemote.operation import Operation
from reemote.result import Result

async def run_command_on_local(operation):
    # Define the asynchronous function to connect to a host and run a command
    host_info = operation.host_info
    global_info = operation.global_info
    command = operation.command
    cp = SSHCompletedProcess()
    executed = False
    caller = operation.caller

    cp.stdout= await operation.callback(host_info, global_info, command, cp, caller)

    return Result(cp=cp, host=host_info.get("host"), op=operation, executed=executed)


async def run_command_on_host(operation):
    # print("running operation", operation)
    # Define the asynchronous function to connect to a host and run a command
    host_info = operation.host_info
    global_info = operation.global_info
    command = operation.command
    cp = SSHCompletedProcess()
    executed = False
    try:
        # Connect to the host
        async with asyncssh.connect(**host_info) as conn:
            # print("connected to host", host_info)
            if operation.composite:
                # print(f"Executing composite command: {command}")
                pass
            else:
                if not operation.guard:
                    pass
                else:
                    # print(f"Executing command: {command}")
                    executed = True
                    if operation.sudo:
                        full_command = f"echo {global_info['sudo_password']} | sudo -S {command}"
                        # print(full_command)
                        # print("sudo begin")
                        cp = await conn.run(full_command, check=False)
                        # print("sudo end")
                        # print(cp)
                    elif operation.su:
                        # print(f"its su {global_info["su_user"]} {command}")
                        full_command = f"su {global_info['su_user']} -c '{command}'"
                        # print(full_command)
                        if global_info["su_user"] == "root":
                            # For root, don't expect password prompt
                            async with conn.create_process(full_command,
                                                           term_type='xterm',
                                                           stdin=asyncssh.PIPE, stdout=asyncssh.PIPE,
                                                           stderr=asyncssh.PIPE) as process:
                                try:
                                    output = await process.stdout.readuntil('Password:')
                                    process.stdin.write(f'{global_info["su_password"]}\n')
                                except asyncio.TimeoutError:
                                    # No password prompt, continue without writing to stdin
                                    pass
                                stdout, stderr = await process.communicate()
                        else:
                            # For non-root users, handle password prompt
                            async with conn.create_process(full_command,
                                                           term_type='xterm',
                                                           stdin=asyncssh.PIPE, stdout=asyncssh.PIPE,
                                                           stderr=asyncssh.PIPE) as process:
                                output = await process.stdout.readuntil('Password:')
                                process.stdin.write(f'{global_info["su_password"]}\n')
                                stdout, stderr = await process.communicate()

                        cp = SSHCompletedProcess(
                            command=full_command ,  # Command executed
                            exit_status=process.exit_status,                     # Exit status
                            returncode=process.returncode,                       # Return code
                            stdout=stdout,                                       # Standard output
                            stderr=stderr                                        # Standard error
                        )

                    else:
                        cp = await conn.run(command, check=False)

    except asyncssh.ProcessError as exc:
        return f"Process on host {host_info.get("host")} exited with status {exc.exit_status}"
    except (OSError, asyncssh.Error) as e:
        return f"Connection failed on host {host_info.get("host")}: {str(e)}"

    # print(f"Output: {cp.stdout}")
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

            if isinstance(value, Operation):
                result = yield value
            elif hasattr(value, 'execute') and callable(value.execute):
                # If it's a node with execute method, push to stack
                stack.append((value, iter(value.execute())))
            else:
                raise TypeError(f"Unsupported yield type: {type(value)}")

        except StopIteration:
            stack.pop()
        except Exception as e:
            # Handle errors in node execution
            print(f"Error in node execution: {e}")
            stack.pop()


async def execute(inventory, obj):
    operations = []
    responses = []

    roots = []
    inventory_items = []
    for inventory_item in inventory:
        roots.append(obj)
        inventory_items.append(inventory_item)  # Store the inventory item
    # Create generators for step-wise traversal of each tree
    generators = [pre_order_generator(root) for root in roots]
    # Result of the previous operation to send
    results = {gen: None for gen in generators}  # Initialize results as None
    # Perform step-wise traversal
    done = False
    while not done:
        all_done = True

        for gen, inventory_item in zip(generators, inventory_items):
            try:
                # print(f"Sending result to generator: {results[gen]}")
                operation = gen.send(results[gen])
                operation.host_info, operation.global_info = inventory_item
                # print(f"Operation: {operation}")
                if operation.local:
                    results[gen] = await run_command_on_local(operation)
                else:
                    results[gen] = await run_command_on_host(operation)

                operations.append(operation)
                # print(f"Result: {results[gen]}")
                responses.append(results[gen])

                all_done = False

            except StopIteration:
                pass
        # If all generators are done, exit the loop
        done = all_done
    return responses
