# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import json

from reemote.utilities.generate_table import generate_grid


def produce_output_grid(json_output: tuple[str, str]):
    """Generates a reStructuredText (RST) grid table from a JSON string.

    This function takes a JSON formatted string, parses it into a Python
    object, and then uses the `generate_grid` utility to create an RST
    grid table. It is designed to handle structured data and present it in a
    human-readable grid format, which is more flexible for complex layouts
    than simple tables.

    It includes error handling to catch and report invalid JSON formats.

    Args:
        json_output (str): A JSON string containing the data to be formatted
            into a grid table.

    Returns:
        str: A string containing the generated reStructuredText grid table.

    Raises:
        json.JSONDecodeError: If the input string `json_output` is not a
            valid JSON.
    """
    try:
        parsed_data = json.loads(json_output)
        grid = generate_grid(parsed_data)

    except json.JSONDecodeError as e:
        print("Error: Invalid JSON format.")
        raise e
    return grid
