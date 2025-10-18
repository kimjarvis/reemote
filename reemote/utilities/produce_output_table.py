# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import json

from reemote.utilities.generate_table import generate_table


def produce_output_table(json_output: tuple[str, str]):
    """Generates a reStructuredText (RST) simple table from a JSON string.

    This function parses a JSON formatted string and passes the resulting
    data to the `generate_table` utility to create an RST simple table.
    It is useful for converting structured JSON data into a standard,
    formatted RST table for documentation or reporting.

    If the input string is not valid JSON, it prints an error and
    re-raises the exception.

    Args:
        json_output (str): A JSON string containing the data to be formatted
            into a simple table.

    Returns:
        str: A string containing the generated reStructuredText simple table.

    Raises:
        json.JSONDecodeError: If the `json_output` string is not in a valid
            JSON format.
    """
    try:
        parsed_data = json.loads(json_output)
        table = generate_table(parsed_data)
        # rst_file_path = os.path.join(output_dir, "out.rst")
        # with open(rst_file_path, "w") as rst_file:
        #     rst_file.write(table)

    except json.JSONDecodeError as e:
        print("Error: Invalid JSON format.")
        raise e
    return table
