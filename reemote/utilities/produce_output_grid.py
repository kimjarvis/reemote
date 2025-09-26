import json

from reemote.utilities.generate_table import generate_grid


def produce_output_grid(json_output: tuple[str, str]):
    # Parse the JSON data first, then pass it to generate_table
    # Generate the RST table and write it to out.rst
    try:
        parsed_data = json.loads(json_output)
        grid = generate_grid(parsed_data)

    except json.JSONDecodeError as e:
        print("Error: Invalid JSON format.")
        raise e
    return grid
