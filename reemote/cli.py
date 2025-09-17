import argparse
import asyncio
import sys
import os

from reemote.validate_inventory_file_and_get_inventory import validate_inventory_file_and_get_inventory
from reemote.validate_root_class_name_and_get_root_class import validate_root_class_name_and_get_root_class
from reemote.verify_inventory_connect import verify_inventory_connect
from reemote.execute import execute
from reemote.verify_python_file import verify_python_file
from reemote.verify_source_file_contains_valid_class import verify_source_file_contains_valid_class
from reemote.validate_inventory_structure import validate_inventory_structure
from reemote.write_responses_to_file import write_responses_to_file
from reemote.produce_json import produce_json
from reemote.produce_table import produce_table
import argparse

from typing import List, Tuple, Dict, Any

def main():
    # Create the argument parser
    parser = argparse.ArgumentParser(
        description='Process inventory and source files with a specified class',
        usage="usage: reemote [-h] [-i INVENTORY_FILE] [-s SOURCE_FILE] [-c CLASS_NAME] [--gui | --cli]",
        epilog='Example: reemote --cli -i ~/inventory.py -s examples/cli/make_directory.py -c Make_directory'
    )

    # Add mutually exclusive group for --gui and --cli
    group = parser.add_mutually_exclusive_group()

    # Add the --gui flag (default is True)
    group.add_argument(
        '--gui',
        action='store_true',
        help='Use the GUI to upload inventory and view execution results (default)',
        default=True
    )

    # Add the --cli flag (default is False)
    group.add_argument(
        '--cli',
        action='store_true',
        help='Use the CLI to process inventory and source files',
        default=False
    )

    # Make inventory_file an optional keyword parameter
    parser.add_argument(
        '-i', '--inventory',
        dest='inventory_file',  # Map to args.inventory_file
        metavar='INVENTORY_FILE',
        help='Path to the inventory Python file (.py extension required)',
        default=None
    )

    # Make source_file an optional keyword parameter
    parser.add_argument(
        '-s', '--source',
        dest='source_file',  # Map to args.source_file
        metavar='SOURCE_FILE',
        help='Path to the source Python file (.py extension required)',
        default=None
    )

    # Make class_name an optional keyword parameter
    parser.add_argument(
        '-c', '--class',
        dest='class_name',  # Use dest to avoid conflict with Python's reserved keyword 'class'
        metavar='CLASS_NAME',
        help='Name of the class in source file that has an execute(self) method',
        default=None
    )

    # Add --output / -o argument
    parser.add_argument(
        '-o', '--output',
        dest='output_file',
        metavar='OUTPUT_FILE',
        help='Path to the output file where results will be saved',
        default=None
    )

    # Add --type / -t argument with choices
    parser.add_argument(
        '-t', '--type',
        dest='output_type',
        metavar='TYPE',
        choices=['grid', 'json', 'rst'],
        help='Output format type: "grid", "json", or "rst"',
        default=None
    )

    # Parse arguments
    args = parser.parse_args()

    # Validation logic
    if args.cli:
        # For CLI mode, all three parameters must be specified
        if not (args.inventory_file and args.source_file and args.class_name):
            parser.error("--cli requires --inventory_file, --source_file, and --class_name to be specified")
    elif args.gui:
        # For GUI mode, source_file and class_name must be specified, but inventory_file is optional
        if not (args.source_file and args.class_name):
            parser.error("--gui requires --source_file and --class_name to be specified")
        if args.inventory_file:
            parser.error("--gui requires --inventory_file not to be specified")

    # Verify inventory file
    if args.inventory_file:
        if not verify_python_file(args.inventory_file):
            sys.exit(1)

    # Verify source file
    if args.source_file:
        if not verify_python_file(args.source_file):
            sys.exit(1)

    # Verify class and method
    if args.source_file and args.class_name:
        if not verify_source_file_contains_valid_class(args.source_file, args.class_name):
            sys.exit(1)

    # Verify the source and class
    if args.source_file and args.class_name:
        root_class = validate_root_class_name_and_get_root_class(args.class_name, args.source_file)
        if not root_class:
            sys.exit(1)

    # verify the inventory
    if args.inventory_file:
        inventory = validate_inventory_file_and_get_inventory(args.inventory_file)
        if not inventory:
            sys.exit(1)
    else:
        inventory = []

    if args.inventory_file:
        if not validate_inventory_structure(inventory()):
            print("Inventory structure is invalid")
            sys.exit(1)

    if args.cli:
        asyncio.run(run_cli(args, inventory, root_class))

    if args.gui:
        from nicegui import ui, native, app
        from reemote.gui import Gui
        from reemote.execute import execute
        from reemote.produce_grid import produce_grid

        async def Control_directory(gui):
            responses = await execute(app.storage.user["inventory"], root_class())
            app.storage.user["columnDefs"], app.storage.user["rowData"] = produce_grid(produce_json(responses))
            gui.execution_report.refresh()

        @ui.page('/')
        def page():
            gui = Gui()
            gui.upload_inventory()
            ui.button('Run', on_click=lambda: Control_directory(gui))
            gui.execution_report()

        ui.run(title="Manage directory", reload=False, port=native.find_open_port(),
               storage_secret='private key to secure the browser session cookie')


async def run_cli(args, inventory: tuple[Any, str], root_class):
    if args.inventory_file:
        if not await verify_inventory_connect(inventory()):
            print("Inventory connections are invalid")
            sys.exit(1)

    responses = await execute(inventory(), root_class())
    json_output = produce_json(responses)
    table_output = produce_table(json_output)
    print(table_output)
    if args.output_type=="json":
        write_responses_to_file(responses = responses, type="json", filepath="development/output/out.json")
    if args.output_type=="rst":
        write_responses_to_file(responses = produce_json(responses), type="rst", filepath="development/output/out.rst")
    if args.output_type=="grid":
        write_responses_to_file(responses = produce_json(responses), type="grid", filepath="development/output/out.py")

    sys.exit(0)



# Synchronous wrapper for console_scripts
def _main():
    # asyncio.run(main())
    main()

if __name__ == "__main__":
    _main()