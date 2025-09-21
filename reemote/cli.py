#!/usr/bin/env python3
import argparse
import os
import sys
import argparse
import asyncio
import sys
import os
import ast

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

class Wrapper:

    def __init__(self, command):
        self.command = command

    def execute(self):
        # Execute a shell command on all hosts
        r = yield self.command()
        # The result is available in stdout
        print(r.cp.stdout)

class Shell:

    def __init__(self, command):
        self.command = command

    def execute(self):
        from reemote.operations.server.shell import Shell
        # Execute a shell command on all hosts
        r = yield Shell(self.command)
        # The result is available in stdout
        print(r.cp.stdout)

def parse_kwargs_string(param_str):
    """Parse 'key=value,key2=value2' string into dict."""
    if not param_str:
        return {}
    kwargs = {}
    for pair in param_str.split(','):
        key, value_str = pair.split('=', 1)
        key = key.strip()
        value_str = value_str.strip()

        # Safely evaluate the value (handles True, False, None, numbers, strings)
        try:
            value = ast.literal_eval(value_str)
        except (ValueError, SyntaxError):
            # Fallback: treat as string if literal_eval fails
            value = value_str

        kwargs[key] = value
    return kwargs

async def main():
    parser = argparse.ArgumentParser(
        description="CLI tool with inventory, source, class, and command options.",
        allow_abbrev=False  # Prevents ambiguous abbreviations
    )
    # Create the argument parser
    parser = argparse.ArgumentParser(
        description='Process inventory and source files with a specified class',
        usage="usage: reemote [-h] [-i INVENTORY_FILE] [-s SOURCE_FILE] [-c CLASS_NAME]",
        epilog="""
        Example: reemote -i ~/inventory.py -s development/examples/main.py -c Info_example
                 reemote -i ~/inventory.py -- echo "hello"      
        """,formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument(
        "-i", "--inventory",
        required=True,
        dest="inventory",
        help="Path to the inventory Python file (.py extension required)"
    )

    parser.add_argument(
        "-s", "--source",
        dest="source",
        default="",
        help="Path to the source Python file (.py extension required)"
    )

    parser.add_argument(
        "-c", "--class",
        dest="_class",  # 'class' is a keyword, so use '_class'
        default="",
        help="Name of the class in source file that has an execute(self) method"
    )

    parser.add_argument('--parameters', default='', help='Comma-separated key=value pairs')

    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to execute (everything after --)"
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

    # Check if no arguments were provided (only script name)
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    args = parser.parse_args()

    # Expand user directory (e.g., ~/inventory.py → /home/kim/inventory.py)
    args.inventory = os.path.expanduser(args.inventory)
    if args.source:
        args.source = os.path.expanduser(args.source)

    # Validation Rule 1: If --class (-c) is specified, --source (-s) and --inventory (-i) must also be specified
    if args._class and (not args.source or not args.inventory):
        parser.error("--class requires --source and --inventory to be specified")

    # Validation Rule 2: If --source (-s) is specified, --class (-c) and --inventory (-i) must also be specified
    if args.source and (not args._class or not args.inventory):
        parser.error("--source requires --class and --inventory to be specified")

    # Validation Rule 3: If a command is provided (args.command is a list and not empty),
    # then --source and --class must NOT be specified
    if args.command and (args.source or args._class):
        parser.error("Command (after --) cannot be used with --source or --class")

    # Validation Rule 4: --inventory (-i) is always required in both modes
    if not args.inventory:
        parser.error("--inventory is required")

    # Validation Rule 5: Exactly one mode must be used: either (-s + -c) OR (command), not both, not neither
    has_script_mode = bool(args.source and args._class)
    has_command_mode = bool(args.command)

    # Custom validation: if output is specified, type must be too
    if args.output_file is not None and args.output_type is None:
        parser.error("Argument -t/--type is required when -o/--output is specified")

    if not (has_script_mode or has_command_mode):
        parser.error("You must specify either (-s and -c) OR a command (after --)")

    if has_script_mode and has_command_mode:
        parser.error("Cannot mix script mode (-s/-c) with command mode (after --)")

    # Print parsed args for debugging/demo (remove in production)
    # print(f"args.inventory = {repr(args.inventory)}")
    # print(f"args.source = {repr(args.source)}")
    # print(f"args._class = {repr(args._class)}")
    # print(f"args.command = {repr(args.command)}")

    # Verify inventory file
    if args.inventory:
        if not verify_python_file(args.inventory):
            sys.exit(1)

    # Verify source file
    if args.source:
        if not verify_python_file(args.source):
            sys.exit(1)

    # Verify class and method
    if args.source and args._class:
        if not verify_source_file_contains_valid_class(args.source, args._class):
            sys.exit(1)

    # Verify the source and class
    if args.source and args._class:
        root_class = validate_root_class_name_and_get_root_class(args._class, args.source)
        if not root_class:
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

    # Parse parameters into kwargs
    kwargs = parse_kwargs_string(args.parameters)

    if args.source:
        responses = await execute(inventory(), Wrapper(root_class))

    if args.command:
        responses = await execute(inventory(), Shell(" ".join(args.command[1:])))

    if args.output_type=="json":
        write_responses_to_file(responses = responses, type="json", filepath=args.output_file)
    elif args.output_type=="rst":
        write_responses_to_file(responses = produce_json(responses), type="rst", filepath=args.output_file)
    elif args.output_type=="grid":
        write_responses_to_file(responses = produce_json(responses), type="grid", filepath=args.output_file)
    else:
        print(produce_table(produce_json(responses)))

def _main():
    """Synchronous wrapper for console_scripts."""
    asyncio.run(main())

if __name__ == "__main__":
    _main()