# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import sys

from reemote.execute import execute
from reemote.utilities.parse_kwargs_string import parse_kwargs_string
from reemote.utilities.validate_root_class_name_and_get_root_class import validate_root_class_name_and_get_root_class
from reemote.utilities.verify_source_file_contains_valid_class import verify_source_file_contains_valid_class
from reemote.utilities.produce_grid import produce_grid
from reemote.utilities.produce_json import produce_json
from reemote.utilities.produce_output_grid import produce_output_grid


async def run_the_deploy(inventory, execution_report, stdout_report, sources):
    if sources.source != "/":

        if sources.source and sources.deployment:
            if not verify_source_file_contains_valid_class(sources.source, sources.deployment):
                sys.exit(1)

        # Verify the source and class
        if sources.source and sources.deployment:
            root_class = validate_root_class_name_and_get_root_class(sources.deployment, sources.source)

        try:
            # Parse parameters into kwargs
            kwargs = parse_kwargs_string(sources.kwargs)
            responses = []
            responses = await execute(inventory.inventory, root_class(**kwargs))

        except NameError:
            # Handle the case where the variable is not defined
            print("The variable 'root_class' is not defined.")
            sys.exit(1)


        columns, rows = produce_grid(produce_json(responses))
        execution_report.set(columns, rows)
        execution_report.execution_report.refresh()
        columns, rows = produce_output_grid(produce_json(responses))
        stdout_report.set(columns, rows)
        stdout_report.execution_report.refresh()
