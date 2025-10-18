# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import json

from reemote.utilities.generate_execution_results import generate_grid


def produce_grid(json_output: tuple[str, str]):
    """Generates a grid representation from a JSON formatted string.

    This function serves as a wrapper to convert a JSON string into a grid
    format. It first parses the input string using `json.loads()` and then
    passes the resulting Python object to the `generate_grid` utility
    function, which handles the actual grid construction.

    The function includes error handling for malformed JSON. If parsing fails,
    it prints an error message and re-raises the `json.JSONDecodeError`.

    Args:
        json_output (str): A string containing the data in a valid JSON format.
            Note: The function's type hint `tuple[str, str]` appears to be
            inconsistent with its implementation, which expects a single string.

    Returns:
        any: The output from the `generate_grid` function, which is expected
             to be a grid representation (e.g., a string).

    Raises:
        json.JSONDecodeError: If the input `json_output` is not a valid JSON
                              string.
    """
    try:
        parsed_data = json.loads(json_output)
        grid = generate_grid(parsed_data)

    except json.JSONDecodeError as e:
        print("Error: Invalid JSON format.")
        raise e
    return grid
