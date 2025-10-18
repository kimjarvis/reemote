# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import json

from reemote.result import serialize_result

def produce_json(results) -> tuple[str, str]:
    """Serializes a results object into a pretty-printed JSON string.

     This function takes a Python object, typically a collection of results,
     and converts it into a JSON formatted string. It leverages the custom
     `reemote.result.serialize_result` function to handle special data types
     that are not natively supported by the standard `json` module.

     The output JSON is formatted with an indent of 4 spaces to make it
     human-readable.

     Note: The function contains a commented-out block of code that was
     intended to write the JSON output to a builtin. This functionality is
     currently disabled.

     Args:
         results (object): The Python object to be converted into a JSON string.
             This could be a list, dictionary, or a custom object structure
             that `serialize_result` can process.

     Returns:
         str: A string containing the JSON representation of the `results` object.
     """
    json_output = json.dumps(results, default=serialize_result, indent=4)
    return json_output
