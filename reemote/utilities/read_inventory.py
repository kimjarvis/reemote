import sys


def read_inventory(inventory_file_path):
    """Reads, executes, and extracts the `inventory()` function from a file.

    This function dynamically loads a Python script from the given file path.
    It executes the script's code in an isolated namespace to prevent side
    effects on the main program. The primary purpose is to locate and return a
    callable function named `inventory` defined within that script. This
    pattern allows for flexible, user-defined inventory sources.

    The function will terminate the program via `sys.exit(1)` and print an
    error to stderr under several conditions:

    - The specified file path does not exist or is unreadable.
    - The file contains Python syntax errors.
    - The file executes without defining a function named `inventory`.
    - An exception occurs during the execution of the inventory script.

    Args:
        inventory_file_path (str): The path to the Python inventory file to be
            executed.

    Returns:
        function: The `inventory()` function object defined within the file.

    Raises:
        SystemExit: If the file cannot be processed for any of the reasons
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
            print(f"Error: The inventory file '{inventory_file_path}' does not define an 'inventory()' function.",
                  file=sys.stderr)
            sys.exit(1)

        inventory_func = inventory_namespace['inventory']
        return inventory_func

    except SyntaxError as e:
        print(f"Syntax error in inventory file '{inventory_file_path}': {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error executing inventory file '{inventory_file_path}': {e}", file=sys.stderr)
        sys.exit(1)