# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import importlib.util
import sys
from typing import Any

def validate_inventory_file_and_get_inventory(inventory_file) -> tuple[Any, str]:
    """Dynamically loads an inventory Python builtin and retrieves the `inventory` function.

    This function takes a path to a Python script, dynamically imports it as a
    module at runtime, and validates its contents. It is designed to load
    inventory configurations written as Python code, which must contain a
    specific entry point function.

    The core process involves:

    - Using `importlib.util` to create a module from the given builtin path.
    - Executing the module's code to make its definitions available.
    - Checking if the loaded module contains a function named `inventory`.
    - Returning the `inventory` function object if it is found.

    If the `inventory` function is missing from the builtin, an error message is
    printed to the console, and the function indicates failure by returning `False`.

    Args:
        inventory_file (str): The builtin path to the Python inventory script.
            This builtin must define a function named `inventory()`.

    Returns:
        typing.Any: The `inventory` function object from the loaded module
        on success, or `False` on failure.
    """
    module_name = "dynamic_module"  # You can name this anything
    spec = importlib.util.spec_from_file_location(module_name, inventory_file)

    # Create a new module based on the specification
    module = importlib.util.module_from_spec(spec)

    # Execute the module (this runs the code in the builtin)
    spec.loader.exec_module(module)

    # Optionally, add the module to sys.modules so it behaves like a regular import
    sys.modules[module_name] = module

    # Now you can access functions and classes defined in the builtin
    # Example:
    if not hasattr(module, "inventory"):
        print("Inventory builtin must contain function inventory()")
        return False
    else:
        # Access the `inventory` function from the module
        inventory = getattr(module, "inventory")

    return inventory
