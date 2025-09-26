import asyncio

from reemote.utilities.parse_kwargs_string import parse_kwargs_string
from reemote.utilities.validate_inventory_file_and_get_inventory import validate_inventory_file_and_get_inventory
from reemote.utilities.validate_root_class_name_and_get_root_class import validate_root_class_name_and_get_root_class
from reemote.execute import execute
from reemote.utilities.verify_python_file import verify_python_file
from reemote.utilities.verify_source_file_contains_valid_class import verify_source_file_contains_valid_class
from reemote.utilities.validate_inventory_structure import validate_inventory_structure
from reemote.utilities.write_responses_to_file import write_responses_to_file
from reemote.utilities.produce_table import produce_table
from reemote.utilities.produce_output_table import produce_output_table
from reemote.utilities.produce_json import produce_json

from reemote.utilities.convert_to_df import convert_to_df
from reemote.utilities.convert_to_tabulate import convert_to_tabulate

async def main():
    import argparse
    import sys

    # Create the argument parser
    parser = argparse.ArgumentParser(
        description="CLI tool with inventory, source, class, and kwarg options.",
        usage="usage: reemote [-h] -i INVENTORY_FILE -s SOURCE_FILE -c CLASS_NAME -k KWARGS",
        epilog="""
        Example: 
          reemote -i ~/inventory_alpine1.py -s reemote/operations/users/add_user.py -c Add_user -k user='abc',password='def' -o output.txt -t json
        """,
        formatter_class=argparse.RawTextHelpFormatter,
        allow_abbrev=False  # Prevents ambiguous abbreviations
    )

    # Required arguments
    parser.add_argument(
        "-i", "--inventory",
        required=True,
        dest="inventory",
        help="Path to the inventory Python file (.py extension required)"
    )

    parser.add_argument(
        "-s", "--source",
        required=True,
        dest="source",
        help="Path to the source Python file (.py extension required)"
    )

    parser.add_argument(
        "-c", "--class",
        required=True,
        dest="_class",  # 'class' is a keyword, so use '_class'
        help="Name of the deployment class in source file that has an execute(self) method"
    )

    parser.add_argument(
        "-k", "--kwargs",
        required=False,
        dest="kwargs",
        help="Path to the kwargs file for the deployment class constructor"
    )

    # Optional arguments with dependencies
    parser.add_argument(
        '-o', '--output',
        dest='output_file',
        metavar='OUTPUT_FILE',
        help='Path to the output file where results will be saved',
        default=None
    )

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

    # Validate that output and type are either both specified or neither is specified
    if (args.output_file is not None) != (args.output_type is not None):
        parser.error("--output (-o) and --type (-t) must both be specified if either is used.")

    # Display help if no arguments are provided
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

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
    kwargs = parse_kwargs_string(args.kwargs)

    responses=[]
    if args.source:
        responses = await execute(inventory(), root_class(**kwargs))


    if args.output_type=="json":
        write_responses_to_file(responses = responses, type="json", filepath=args.output_file)
    elif args.output_type=="rst":
        write_responses_to_file(responses = produce_json(responses), type="rst", filepath=args.output_file)
    elif args.output_type=="grid":
        write_responses_to_file(responses = produce_json(responses), type="grid", filepath=args.output_file)
    else:
        json = produce_json(responses)
        # print(json)
        df = convert_to_df(json,columns=["command", "host", "guard", "executed", "changed"])
        table = convert_to_tabulate(df)
        # print(table)
        df = convert_to_df(json,columns=["command", "host", "returncode", "stdout", "stderr", "error"])
        table = convert_to_tabulate(df)
        print(table)

        # df = convert_to_df(json)
        # table = convert_to_tabulate(df)
        # print(table)


def _main():
    """Synchronous wrapper for console_scripts."""
    asyncio.run(main())

if __name__ == "__main__":
    _main()
