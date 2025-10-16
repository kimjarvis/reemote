# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import asyncio

from reemote.utilities.parse_kwargs_string import parse_kwargs_string
from reemote.utilities.validate_inventory_file_and_get_inventory import validate_inventory_file_and_get_inventory
from reemote.utilities.validate_root_class_name_and_get_root_class import validate_root_class_name_and_get_root_class
from reemote.execute import execute
from reemote.utilities.verify_python_file import verify_python_file
from reemote.utilities.verify_source_file_contains_valid_class import verify_source_file_contains_valid_class
from reemote.utilities.validate_inventory_structure import validate_inventory_structure
from reemote.utilities.write_responses_to_file import write_responses_to_file
from reemote.utilities.produce_json import produce_json

from reemote.utilities.convert_to_df import convert_to_df
from reemote.utilities.convert_to_tabulate import convert_to_tabulate

async def main(callable=None):
    """
    Main entry point for deployments.

    This function parses command-line arguments, validates input files and parameters,
    executes remote operations, and outputs results in the specified format.

    The CLI requires three main arguments:
    - Inventory file: Defines the target hosts for deployment
    - Source file: Python file containing the deployment class
    - Class name: The deployment class with an execute method

    Optional arguments allow specifying output file and format.

    Supported output formats:
    - grid: Tabular format with grid borders
    - json: Raw JSON output
    - rst: reStructuredText table format

    Returns:
        None: Results are printed to stdout or written to a file
    """
    import argparse
    import sys

    # Create the argument parser
    parser = argparse.ArgumentParser(
        description="CLI tool with inventory, source, class, and kwarg options.",
        usage="usage: reemote [-h] -i INVENTORY_FILE -s SOURCE_FILE -c CLASS_NAME -k KWARGS",
        epilog="""
        Example: 
          python3 reemote/deployments/microcloud.py -i ~/inventory_alpine1.py 
        """,
        formatter_class=argparse.RawTextHelpFormatter,
        allow_abbrev=False  # Prevents ambiguous abbreviations
    )

    # Required arguments
    parser.add_argument(
        "-i", "--inventory",
        required=True,
        dest="inventory",
        help="Path to the inventory Python builtin (.py extension required)"
    )

    # Parse arguments
    args = parser.parse_args()

    # Display help if no arguments are provided
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    # Verify inventory builtin
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

    responses = await execute(inventory(), callable())

    json = produce_json(responses)
    df = convert_to_df(json,columns=["command", "host", "returncode", "stdout", "stderr", "error"])
    table = convert_to_tabulate(df)
    print(table)

def _main():
    """Synchronous wrapper for console_scripts."""
    asyncio.run(main())

if __name__ == "__main__":
    _main()
