#!/usr/bin/env python3

import argparse
import asyncio
import sys

from reemote.validate_inventory_file_and_get_inventory import validate_inventory_file_and_get_inventory
from reemote.validate_root_class_name_and_get_root_class import validate_root_class_name_and_get_root_class
from reemote.verify_inventory_connect import verify_inventory_connect
from reemote.run import run
from reemote.printers import construct_host_ops, summarize_data_for_aggrid, get_printable_aggrid
from reemote.verify_python_file import verify_python_file
from reemote.verify_source_file_contains_valid_class import verify_source_file_contains_valid_class
from reemote.validate_inventory_structure import validate_inventory_structure

async def main():
    parser = argparse.ArgumentParser(
        description='Process inventory and source files with a specified class',
        epilog='Example: reemote ~/inventory.py examples/cli/main.py Make_directory'
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
        return False

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
