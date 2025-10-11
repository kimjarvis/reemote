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