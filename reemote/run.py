from reemote.operation import Operation
from reemote.result import Result
from asyncssh import SSHCompletedProcess
import asyncssh


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

            # Handle different types of yielded values
            if isinstance(value, Operation):
                result = yield value
            elif isinstance(value, str):
                # Auto-wrap strings in Operation objects
                operation = Operation(value)
                result = yield operation
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

# Define the asynchronous function to connect to a host and run a command
async def run_command_on_host(operation):
    host_info = operation.host_info
    sudo_info = operation.sudo_info
    command = operation.command
    cp = SSHCompletedProcess()

    try:
        # Connect to the host
        async with asyncssh.connect(**host_info) as conn:
            # Run the command
            # Check if the command starts with 'sudo'
            # print(f"Executing command: {command}")
            if command.startswith("sudo"):
                if not sudo_info.get("sudo_password"):
                    raise ValueError("Command requires sudo, but no sudo password was provided.")

                # Run the command with sudo, providing the password via stdin
                cp = await conn.run(
                    command,
                    input=sudo_info.get("sudo_password") + "\n",
                    # Provide the sudo password followed by a newline
                    check=False  # Do not raise an exception if the command fails
                )
            elif command.startswith("su"):
                print(f"hi {sudo_info.get("su_password")} {command}")
                if not sudo_info.get("su_password"):
                    raise ValueError("Command requires su, but no su password was provided.")

                # Run the command with sudo, providing the password via stdin
                cp = await conn.run(
                    command,
                    input=sudo_info.get("su_password") + "\n",
                    # Provide the sudo password followed by a newline
                    check=False  # Do not raise an exception if the command fails
                )


            else:
                cp = await conn.run(command, check=False)

    except (OSError, asyncssh.Error) as e:
        return f"Connection failed on host {host_info.get("host")}: {str(e)}"

    # print(f"Output: {cp.stdout}")
    return Result(cp=cp, host=host_info.get("host"))


async def run(inventory, obj):
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
                operation.host_info, operation.sudo_info = inventory_item
                results[gen] = await run_command_on_host(operation)

                # print(f"Operation: {operation}")
                operations.append(operation)
                # print(f"Result: {results[gen]}")
                responses.append(results[gen])

                all_done = False

            except StopIteration:
                pass
        # If all generators are done, exit the loop
        done = all_done
    return operations, responses
