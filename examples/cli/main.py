import argparse
import asyncio
import importlib.util
import sys
from pathlib import Path
import inspect
import asyncio
from typing import Any
import importlib.util
import sys

from reemote.validate_inventory_structure import validate_inventory_structure
from reemote.verify_inventory_connect import verify_inventory_connect
from reemote.run import run
from reemote.printers import construct_host_ops, print_json_ops, print_json_ops, summarize_data_for_aggrid, get_printable_aggrid



def verify_python_file(file_path):
    """Verify that the file has .py extension and exists"""
    path = Path(file_path)
    if not path.exists():
        print(f"Error: File '{file_path}' does not exist")
        return False
    if path.suffix != '.py':
        print(f"Error: File '{file_path}' must have .py extension")
        return False
    return True


def verify_source_file_contains_valid_class(source_file, class_name):
    """Verify that the class exists and has execute method with no parameters"""
    try:
        # Load the module from file
        spec = importlib.util.spec_from_file_location("source_module", source_file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Check if class exists
        if not hasattr(module, class_name):
            print(f"Error: Class '{class_name}' not found in {source_file}")
            return False

        cls = getattr(module, class_name)

        # Check if execute method exists
        if not hasattr(cls, 'execute'):
            print(f"Error: Class '{class_name}' does not have an execute method")
            return False

        # Check if execute method takes no parameters (besides self)
        execute_method = getattr(cls, 'execute')
        sig = inspect.signature(execute_method)

        # Should have only 'self' parameter
        if len(sig.parameters) != 1:
            print(
                f"Error: execute method should take only 'self' parameter, but takes {len(sig.parameters)} parameters")
            return False

        return True

    except Exception as e:
        print(f"Error loading module from {source_file}: {e}")
        return False


def validate_root_class_name_and_get_root_class(class_name, source_file) -> Any:
    module_name = "dynamic_module"  # You can name this anything
    spec = importlib.util.spec_from_file_location(module_name, source_file)
    # Create a new module based on the specification
    module = importlib.util.module_from_spec(spec)
    # Execute the module (this runs the code in the file)
    spec.loader.exec_module(module)

    # Optionally, add the module to sys.modules so it behaves like a regular import
    sys.modules[module_name] = module

    # Now you can access functions and classes defined in the file
    # Example:
    if not hasattr(module, class_name):
        print(f"Source file must contain class {class_name}")
        return False
    else:
        # Access the `inventory` function from the module
        root_class = getattr(module, class_name)
    return root_class


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

    if not validate_inventory_structure(inventory()):
        print("Inventory structure is invalid")
        return False
    return inventory


async def main():
    parser = argparse.ArgumentParser(
        description='Process inventory and source files with a specified class',
        epilog='Example: python3 examples/cli/main.py ~/inventory.py examples/cli/create_tmp_mydir.py Make_directory'
    )

    parser.add_argument(
        'inventory_file',
        help='Path to the inventory Python file (.py extension required)'
    )

    parser.add_argument(
        'source_file',
        help='Path to the source Python file (.py extension required)'
    )

    parser.add_argument(
        'class_name',
        help='Name of the class in source file that has an execute(self) method'
    )

    # Parse arguments
    args = parser.parse_args()

    # Verify inventory file
    if not verify_python_file(args.inventory_file):
        sys.exit(1)

    # Verify source file
    if not verify_python_file(args.source_file):
        sys.exit(1)

    # Verify class and method
    if not verify_source_file_contains_valid_class(args.source_file, args.class_name):
        sys.exit(1)

    # Verify the source and class
    root_class = validate_root_class_name_and_get_root_class(args.class_name, args.source_file)
    if not root_class:
        sys.exit(1)

    # verify the inventory
    inventory = validate_inventory_file_and_get_inventory(args.inventory_file)
    if not inventory:
        sys.exit(1)

    if not await verify_inventory_connect(inventory()):
        print("Inventory connections are invalid")
        return False

    operations, responses = await run(inventory(), root_class())
    host_ops = construct_host_ops(operations,responses)
    dgrid=summarize_data_for_aggrid(host_ops)
    grid=get_printable_aggrid(dgrid)
    print(grid)

if __name__ == "__main__":
    asyncio.run(main())
