import argparse
import asyncio
import sys
from reemote.execute import execute
from reemote.utilities.produce_table import produce_table
from reemote.utilities.produce_output_table import produce_output_table
from reemote.utilities.produce_json import produce_json
from reemote.utilities.verify_python_file import verify_python_file
from reemote.utilities.validate_inventory_file_and_get_inventory import validate_inventory_file_and_get_inventory
from reemote.utilities.validate_inventory_structure import validate_inventory_structure
from reemote.operations.filesystem.directory import Directory

def str_to_bool(value):
    """
    Convert a string representation of a boolean to a Python boolean.
    Accepts 'True', 'true', 'False', 'false' as valid inputs.
    Raises an error for invalid inputs.
    """
    value = value.lower()
    if value in ('true', 't'):
        return True
    elif value in ('false', 'f'):
        return False
    else:
        raise argparse.ArgumentTypeError(f"Invalid boolean value: {value}")

async def main():
    # Create an ArgumentParser object
    parser = argparse.ArgumentParser(description="Process inventory and directory information asynchronously.")

    # Add arguments
    parser.add_argument(
        "-i", "--inventory",
        type=str,
        required=True,
        help="Path to the inventory file (must exist)."
    )
    parser.add_argument(
        "-d", "--directory",
        type=str,
        required=True,
        help="Path to the directory."
    )
    parser.add_argument(
        "-p", "--present",
        type=str_to_bool,  # Custom function to convert string to boolean
        required=True,
        help="Boolean flag (True or False)."
    )

    # Parse the arguments
    args = parser.parse_args()

    # Print the parsed arguments for verification
    print("Inventory File:", args.inventory)
    print("Directory:", args.directory)
    print("Present Flag:", args.present)



    # Verify inventory file
    if args.inventory:
        if not verify_python_file(args.inventory):
            sys.exit(1)

    # verify the inventory
    if args.inventory:
        inventory = validate_inventory_file_and_get_inventory(args.inventory)
        if not inventory:
            sys.exit(1)
    else:
        inventory = []

    if args.inventory:
        if not validate_inventory_structure(inventory()):
            print("Inventory structure is invalid")
            sys.exit(1)

    responses = await execute(inventory(), Directory(path=args.directory, present=args.present))
    print(produce_table(produce_json(responses)))
    print(produce_output_table(produce_json(responses)))


if __name__ == "__main__":
    # Run the asyncio event loop
    asyncio.run(main())
