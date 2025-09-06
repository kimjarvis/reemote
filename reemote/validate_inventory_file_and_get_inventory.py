import importlib.util
import sys
from typing import Any

from reemote.validate_inventory_structure import validate_inventory_structure


def validate_inventory_file_and_get_inventory(inventory_file) -> tuple[Any, str]:
    # Create a module specification from the file location
    module_name = "dynamic_module"  # You can name this anything
    spec = importlib.util.spec_from_file_location(module_name, inventory_file)

    # Create a new module based on the specification
    module = importlib.util.module_from_spec(spec)

    # Execute the module (this runs the code in the file)
    spec.loader.exec_module(module)

    # Optionally, add the module to sys.modules so it behaves like a regular import
    sys.modules[module_name] = module

    # Now you can access functions and classes defined in the file
    # Example:
    if not hasattr(module, "inventory"):
        print("Inventory file must contain function inventory()")
        return False
    else:
        # Access the `inventory` function from the module
        inventory = getattr(module, "inventory")

    return inventory
