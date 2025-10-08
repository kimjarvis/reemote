from tabulate import tabulate

def _generate_table(data):
    """Processes raw command output data into a structured grid format.

    This internal function transforms a list of command execution entries into a
    pivoted table structure. It groups consecutive entries by command and pivots
    the hosts into columns. For each unique host, two columns are created:
    one for the command's return code and one for its standard output.

    The primary purpose is to create the fundamental `columnDefs` and `rowData`
    structures that can be used by other presentation functions.

    Args:
        data (list[dict]): A list of dictionaries, where each dictionary
            represents a command execution on a specific host. Each dictionary
            is expected to have keys: 'host', 'op' (containing a 'command'
            key), and 'cp' (containing 'returncode' and 'stdout' keys).

    Returns:
        tuple: A tuple containing two elements:

            - result (dict): A dictionary with two keys:
                - 'columnDefs' (list[dict]): Definitions for grid columns.
                - 'rowData' (list[dict]): The processed data, with one row
                  per unique, consecutive command.
            - hosts (list[str]): A sorted list of unique hostnames found in
              the data.

    Raises:
        TypeError: If the 'data' variable is not a list of dictionaries,
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
        result["columnDefs"].append({"headerName": f"{host} Returncode", "field": f"{host.replace(".","_")}_executed"})
        result["columnDefs"].append({"headerName": f"{host} Stdout", "field": f"{host.replace(".","_")}_changed"})

    # Step 3: Process data by grouping consecutive entries with the same command
    i = 0
    while i < len(data):
        # Get the current command
        command = data[i]['op']['command'][:60]

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
            executed = entry["cp"]['returncode']
            stdout_value = entry["cp"]['stdout']

            changed = str(stdout_value)[:60] if stdout_value is not None else "None"

            # Add the current host's data
            row[f"{host.replace(".","_")}_executed"] = executed
            row[f"{host.replace(".","_")}_changed"] = changed

            i += 1  # Move to next entry

        # Add the completed row to rowData
        result["rowData"].append(row)

    return result, hosts

def generate_table(data):
    """Converts command output data into a format suitable for a data grid.

    This function processes raw command output data to generate the necessary
    `columnDefs` and `rowData` for use with a data grid library like AG-Grid.
    It pivots the data, creating a single row for each consecutive command and
    placing the results from each host into separate columns.

    The main transformation involves:

    - Grouping data by consecutive command strings.
    - Creating a "Command" column.
    - Creating two columns for each unique host: one for the return code
      and one for the standard output.
    - Populating the rows with the corresponding results for each host.

    Args:
        data (list[dict]): A list of dictionaries representing command
            executions. See `_generate_table` for the required structure.

    Returns:
        tuple: A tuple containing three elements:

            - columnDefs (list[dict]): A list of dictionaries defining the
              properties for each grid column.
            - rowData (list[dict]): A list of dictionaries, where each
              dictionary represents a row of data.
            - hosts (list[str]): A sorted list of unique hostnames, which
              can be useful for constructing the grid UI.
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
    result , hosts = _generate_table(data)
    return result["columnDefs"], result["rowData"],
