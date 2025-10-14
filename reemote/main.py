# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import sys
import argparse
from reemote.execute import execute
from reemote.utilities.verify_python_file import verify_python_file
from reemote.utilities.validate_inventory_file_and_get_inventory import validate_inventory_file_and_get_inventory
from reemote.utilities.validate_inventory_structure import validate_inventory_structure
from reemote.utilities.produce_json import produce_json
from reemote.utilities.convert_to_df import convert_to_df
from reemote.utilities.convert_to_tabulate import convert_to_tabulate

async def main(callable=None):
    """
    Main entry point for the reemote application.

    This function serves as the primary execution handler that processes command-line arguments,
    validates the inventory file, and executes operations across remote hosts.

    Args:
        callable (callable, optional): A callable object that returns an instance with an
            [execute()](file:///home/kim/reemote/reemote/execute.py#L0-L209) method. This method should yield [Command](file:///home/kim/reemote/reemote/command.py#L2-L65) objects to be executed
            across the hosts defined in the inventory. Defaults to None.

    Process:
        1. Parses command-line arguments to get the inventory file path
        2. Validates that the inventory file is a valid Python file
        3. Loads and validates the inventory data structure
        4. Ensures the inventory contains at least one host
        5. Executes operations yielded by the callable's execute() method
        6. Processes and displays results in a tabulated format

    The function expects the inventory file to contain a callable that returns a list of
    tuples, where each tuple contains host connection information and global configuration.

    Returns:
        None: Results are printed directly to stdout in tabular format
    """
    parser = argparse.ArgumentParser(
        description="Open an SSH terminal connection using an inventory builtin.",
        usage="usage: terminal.py [-h] -i INVENTORY_FILE [-n HOST_NUMBER] [--no-test]",
        epilog=""
    )
    parser.add_argument(
        "-i", "--inventory",
        required=True,
        dest="inventory",
        help="Path to the inventory Python builtin (.py extension required)"
    )
    args = parser.parse_args()

    # Verify inventory file exists and is a Python file
    if not verify_python_file(args.inventory):
        print("Invalid inventory file")
        sys.exit(1)

    # Load and validate inventory
    inventory = validate_inventory_file_and_get_inventory(args.inventory)
    if not inventory:
        print("Failed to load inventory")
        sys.exit(1)

    # Validate inventory structure
    inventory_data = inventory()
    if not validate_inventory_structure(inventory_data):
        print("Inventory structure is invalid")
        sys.exit(1)

    # Check if inventory has hosts
    if not inventory_data or not inventory_data[0]:
        print("No hosts in inventory")
        sys.exit(1)

    # Execute operations
    responses = []
    for operation in callable().execute():  # Iterate over the generator
        response = await execute(inventory(), operation)
        responses.append(response)

    # Process results - FIX: Flatten the responses if they contain lists
    flattened_responses = []
    for response in responses:
        if isinstance(response, list):
            flattened_responses.extend(response)
        else:
            flattened_responses.append(response)

    json = produce_json(flattened_responses)
    df = convert_to_df(json, columns=["command", "host", "returncode", "stdout", "stderr", "error"])
    table = convert_to_tabulate(df)
    print(table)