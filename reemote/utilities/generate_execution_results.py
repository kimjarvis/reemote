# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from tabulate import tabulate

def _generate_table(data):
    """Transforms raw event data into a structured grid format.

    This internal helper function processes a list of event dictionaries,
    groups them by command, and pivots the data to create a table structure.
    For each unique host found in the data, it generates 'Executed' and
    'Changed' columns.

    The primary purpose is to group consecutive entries that share the same
    command string into a single row, showing the execution status across
    all relevant hosts.

    Args:
        data (list[dict]): A list of dictionaries, where each dictionary
            represents a single event. Each dictionary must contain keys:
            'host' (str), 'executed' (any), 'changed' (any), and 'op' (dict).
            The 'op' dictionary must contain a 'command' (str) key.

    Returns:
        tuple: A tuple containing two elements:
            - result (dict): A dictionary with 'columnDefs' and 'rowData',
              suitable for a grid component.
            - hosts (list[str]): A sorted list of unique hostnames
              found in the data.

    Raises:
        TypeError: If the input 'data' is not a list of dictionaries,
                   preventing host extraction.
    """
    # Step 1: Extract unique hosts
    try:
        hosts = sorted(set(entry['host'] for entry in data))
    except TypeError as e:
        print("Error: The 'data' variable must be a list of dictionaries.")
        raise e

    # Step 2: Initialize columnDefs and rowData
    result = {
        "columnDefs": [],
        "rowData": []
    }

    # Add the 'Command' column to columnDefs
    result["columnDefs"].append({"headerName": "Command", "field": "command"})

    # Add two columns for each host: Executed and Changed
    for host in hosts:
        result["columnDefs"].append({"headerName": f"{host} Executed", "field": f"{host.replace(".","_")}_executed"})
        result["columnDefs"].append({"headerName": f"{host} Changed", "field": f"{host.replace(".","_")}_changed"})

    # Step 3: Process data by grouping consecutive entries with the same command
    i = 0
    while i < len(data):
        # Get the current command
        command = data[i]['op']['command']

        # Create a new row for this command
        row = {"command": command}

        # Initialize all host columns as empty
        for h in hosts:
            row[f"{h}_executed"] = ""
            row[f"{h}_changed"] = ""

        # Process all consecutive entries with this same command
        while i < len(data) and data[i]['op']['command'] == command:
            entry = data[i]
            host = entry['host']
            executed = entry['executed']
            changed = entry['changed']

            # Add the current host's data
            row[f"{host.replace(".","_")}_executed"] = executed
            row[f"{host.replace(".","_")}_changed"] = changed

            i += 1  # Move to next entry

        # Add the completed row to rowData
        result["rowData"].append(row)

    return result, hosts

def generate_table(data):
    """Generates a human-readable ASCII grid table from event data.

     This function takes a list of event data, processes it into a tabular
     format, and then uses the `tabulate` library to create a formatted
     string representation of the table. It is ideal for displaying
     command execution results in a console or log builtin.

     It internally uses `_generate_table` to structure the data before
     formatting.

     Args:
         data (list[dict]): A list of event dictionaries. See the
             docstring for `_generate_table` for the required structure.

     Returns:
         str: A string containing the formatted ASCII table in 'grid' format.
     """
    result , hosts = _generate_table(data)

    # Step 4: Convert to tabulate format
    headers = [col['headerName'] for col in result["columnDefs"]]
    table_data = []
    for row in result["rowData"]:
        formatted_row = [row['command']]  # Start with command
        for host in hosts:
            executed = row[f"{host.replace(".","_")}_executed"]
            changed = row[f"{host.replace(".","_")}_changed"]
            formatted_row.append(executed if executed != "" else "")
            formatted_row.append(changed if changed != "" else "")
        table_data.append(formatted_row)

    # Step 5: Return the table
    return tabulate(table_data, headers=headers, tablefmt="grid")

def generate_grid(data):
    """Generates a human-readable ASCII grid table from event data.

    This function takes a list of event data, processes it into a tabular
    format, and then uses the `tabulate` library to create a formatted
    string representation of the table. It is ideal for displaying
    command execution results in a console or log builtin.

    It internally uses `_generate_table` to structure the data before
    formatting.

    Args:
        data (list[dict]): A list of event dictionaries. See the
            docstring for `_generate_table` for the required structure.

    Returns:
        str: A string containing the formatted ASCII table in 'grid' format.
    """
    result , hosts = _generate_table(data)
    return result["columnDefs"], result["rowData"],
