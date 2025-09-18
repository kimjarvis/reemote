import os
#
from reemote.produce_grid import produce_grid
from reemote.produce_table import produce_table


def write_responses_to_file(type: str=None, filepath: str=None, responses=None):
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
