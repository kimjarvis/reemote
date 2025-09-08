def summarize_data_for_aggrid(data):
    # Initialize the result dictionary
    result = {"columnDefs": [], "rowData": []}

    # Extract the list of hosts from the input data
    hosts = [host_data["host"] for host_data in data]

    # Add the 'Command' column to columnDefs
    result["columnDefs"].append({"headerName": "Command", "field": "command"})

    # Add a column for each host
    for i, host in enumerate(hosts):
        result["columnDefs"].append({"headerName": host, "field": f"host{i}"})

    # Populate rowData
    for ops_index, ops in enumerate(data[0]["ops"]):  # Assuming all hosts have the same commands
        row = {}
        # Add the command to the row
        if ops[0]["command"].startswith("composite"):
            row["command"] = ops[0]["command"].replace("composite", ">>>>")
        else:
            row["command"] = ops[0]["command"]

        # Add boolean values for each host
        for host_index, host_data in enumerate(data):
            command_dict, result_dict = host_data["ops"][ops_index]
            changed = result_dict.get("changed", False)
            row[f"host{host_index}"] = changed

        # Append the row to rowData
        result["rowData"].append(row)

    return result
