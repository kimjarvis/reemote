import sys

from reemote.execute import execute
from reemote.utilities.parse_kwargs_string import parse_kwargs_string
from reemote.utilities.produce_grid import produce_grid
from reemote.utilities.produce_json import produce_json
from reemote.utilities.produce_output_grid import produce_output_grid
from reemote.utilities.validate_root_class_name_and_get_root_class import validate_root_class_name_and_get_root_class
from reemote.utilities.verify_source_file_contains_valid_class import verify_source_file_contains_valid_class


async def run_the_deploy(inv, er, stdout, sources):
    if sources.source != "/":
        if sources.source and sources.deployment:
            if not verify_source_file_contains_valid_class(sources.source, sources.deployment):
                sys.exit(1)

        # Verify the source and class
        if sources.source and sources.deployment:
            root_class = validate_root_class_name_and_get_root_class(sources.deployment, sources.source)

        if not root_class:
            print("root class not found")
            sys.exit(1)

        # Parse parameters into kwargs
        kwargs = parse_kwargs_string(sources.kwargs)
        responses = []
        responses = await execute(inventory(), root_class(**kwargs))
        c, r =produce_grid(produce_json(responses))
        er.set(c, r)
        er.execution_report.refresh()
        c, r =produce_output_grid(produce_json(responses))
        stdout.set(c, r)
        stdout.execution_report.refresh()
