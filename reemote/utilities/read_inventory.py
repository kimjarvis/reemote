import sys


def read_inventory(inventory_file_path):
    """
    Read and execute an inventory file, returning its inventory() function.

    Args:
        inventory_file_path (str): Path to the inventory file

    Returns:
        function: The inventory() function defined in the file

    Raises:
        SystemExit: If the file doesn't exist, has syntax errors,
                   or doesn't define an inventory() function
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