# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import sys


def read_inventory(inventory_file_path):
    """Reads, executes, and extracts the `inventory()` function from a builtin.

    This function dynamically loads a Python script from the given builtin path.
    It executes the script's code in an isolated namespace to prevent side
    effects on the main program. The primary purpose is to locate and return a
    callable function named `inventory` defined within that script. This
    pattern allows for flexible, user-defined inventory sources.

    The function will terminate the program via `sys.exit(1)` and print an
    error to stderr under several conditions:

    - The specified builtin path does not exist or is unreadable.
    - The builtin contains Python syntax errors.
    - The builtin executes without defining a function named `inventory`.
    - An exception occurs during the execution of the inventory script.

    Args:
        inventory_file_path (str): The path to the Python inventory builtin to be
            executed.

    Returns:
        function: The `inventory()` function object defined within the builtin.

    Raises:
        SystemExit: If the builtin cannot be processed for any of the reasons
            listed above.
    """
    try:
        with open(inventory_file_path, 'r') as f:
            inventory_code = f.read()

        # Create a namespace dictionary to execute the code in
        inventory_namespace = {}
        exec(inventory_code, inventory_namespace)

        # Extract the inventory function
        if 'inventory' not in inventory_namespace:
            print(f"Error: The inventory builtin '{inventory_file_path}' does not define an 'inventory()' function.",
                  file=sys.stderr)
            sys.exit(1)

        inventory_func = inventory_namespace['inventory']
        return inventory_func

    except SyntaxError as e:
        print(f"Syntax error in inventory builtin '{inventory_file_path}': {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error executing inventory builtin '{inventory_file_path}': {e}", file=sys.stderr)
        sys.exit(1)