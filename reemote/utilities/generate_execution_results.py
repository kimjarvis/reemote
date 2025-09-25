from tabulate import tabulate

def _generate_table(data):
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
