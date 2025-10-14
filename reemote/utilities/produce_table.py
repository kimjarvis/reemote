# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import json

from reemote.utilities.generate_execution_results import generate_table


def produce_table(json_output: tuple[str, str]):
    """Creates a reStructuredText (RST) table from JSON-formatted results.

     This function processes a JSON string, which is expected to contain
     structured results from a command or process execution. It parses the
     JSON and utilizes the `generate_table` function to format the data
     into a reStructuredText table.

     The primary use case is to present command or script execution outcomes
     in a structured table format. It includes error handling for malformed
     JSON input.

     Args:
         json_output (str): A JSON string representing execution results to
             be displayed in a table.

     Returns:
         str: A string containing the generated reStructuredText table of
             execution results.

     Raises:
         json.JSONDecodeError: If the input `json_output` string is not
             valid JSON.
     """
    try:
        parsed_data = json.loads(json_output)
        table = generate_table(parsed_data)

    except json.JSONDecodeError as e:
        print("Error: Invalid JSON format.")
        raise e
    return table
