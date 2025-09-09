import argparse
import asyncio
import sys

from reemote.validate_inventory_file_and_get_inventory import validate_inventory_file_and_get_inventory
from reemote.validate_root_class_name_and_get_root_class import validate_root_class_name_and_get_root_class
from reemote.verify_inventory_connect import verify_inventory_connect
from reemote.run import run
from reemote.get_printable_aggrid import get_printable_aggrid
from reemote.summarize_data_for_aggrid import summarize_data_for_aggrid
from reemote.construct_host_ops import construct_host_ops
from reemote.verify_python_file import verify_python_file
from reemote.verify_source_file_contains_valid_class import verify_source_file_contains_valid_class
from reemote.validate_inventory_structure import validate_inventory_structure

async def main():
    parser = argparse.ArgumentParser(
        description='Process inventory and source files with a specified class',
        usage="usage: reemote [-h] inventory_file source_file class_name",
        epilog='Example: reemote ~/inventory.py examples/cli/make_directory.py Make_directory'
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

    if not validate_inventory_structure(inventory()):
        print("Inventory structure is invalid")
        sys.exit(1)

    if not await verify_inventory_connect(inventory()):
        print("Inventory connections are invalid")
        sys.exit(1)

    operations, responses = await run(inventory(), root_class())
    host_ops = construct_host_ops(operations,responses)
    dgrid=summarize_data_for_aggrid(host_ops)
    grid=get_printable_aggrid(dgrid)
    print(grid)
    sys.exit(0)

# Synchronous wrapper for console_scripts
def _main():
    asyncio.run(main())

if __name__ == "__main__":
    _main()