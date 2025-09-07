import pandas as pd


def generate_change_df(host_ops):
    # Step 1: Extract commands, changed, and executed values for each host
    data = {}
    for host_data in host_ops:
        host = host_data["host"]
        data[host] = []
        for op_pair in host_data["ops"]:
            # Extract the command from the first dictionary
            command = op_pair[0]["command"]
            # Merge the changed values from both dictionaries
            changed = any(d.get("changed", False) for d in op_pair)
            # Get the executed value from the second dictionary
            executed = op_pair[1].get("executed", False)
            data[host].append((command, changed, executed))
    # Step 2: Align commands across hosts
    commands = [op[0] for op in data[next(iter(data))]]  # Use the first host's commands as reference
    df_data = {}
    # Add columns for each host: <host>_changed and <host>_executed
    for host, ops in data.items():
        df_data[(host, "changed")] = [changed for _, changed, _ in ops]
        df_data[(host, "executed")] = [executed for _, _, executed in ops]
    # Step 3: Create the DataFrame with MultiIndex columns
    df = pd.DataFrame(df_data, index=commands)
    # Rename the index to "Command"
    df.index.name = "Command"
    # Display the DataFrame
    return df
