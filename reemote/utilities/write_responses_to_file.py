import os
#
from reemote.utilities.produce_grid import produce_grid
from reemote.utilities.produce_table import produce_table
from reemote.utilities.produce_json import produce_json

def write_responses_to_file(type: str=None, filepath: str=None, responses=None):
    """Writes response data to a file in a specified format.

    This function takes a collection of responses and serializes them into a
    file. The output format is determined by the `type` parameter. It relies
    on helper functions to handle the conversion for each specific format.

    Supported formats:

    - **json**: Uses `produce_json` to create a JSON string representation.
    - **rst**: Uses `produce_table` to generate a reStructuredText grid table.
    - **grid**: Uses `produce_grid` to create `columnDefs` and `rowData`, then
      writes the string representation of a dictionary containing these keys,
      suitable for Python interpretation.

    Args:
        type (str, optional): The format for the output file. Accepted
            values are "json", "rst", or "grid".
        filepath (str, optional): The full path, including the filename, for
            the output file.
        responses (any, optional): The data structure containing the responses
            to be serialized and written.
    """
    if type == "json":
        file_path = os.path.join(filepath)
        with open(file_path, "w") as file:
            file.write(produce_json(responses))
    if type == "rst":
        file_path = os.path.join(filepath)
        with open(file_path, "w") as file:
            file.write(produce_table(responses))
    if type == "grid":
        file_path = os.path.join(filepath)
        with open(file_path, "w") as file:
            columnDefs,rowData=produce_grid(responses)
            table={"columnDefs":columnDefs, "rowData":rowData}
            file.write(repr(table))
