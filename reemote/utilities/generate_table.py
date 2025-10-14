# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import pandas as pd

import pandas as pd

import pandas as pd

def _generate_table(data):
    """
    Processes raw command output data into a structured grid format using pandas.

    Groups consecutive entries by command and pivots hosts into columns.
    Each host gets two columns: one for return codes and one for stdout.

    Args:
        data (list[dict]): A list of dictionaries representing command executions.

    Returns:
        tuple: A tuple containing:
            - columnDefs (list[dict]): Definitions for grid columns.
            - rowData (pd.DataFrame): The processed data as a DataFrame.
    """
    # Step 1: Flatten nested JSON structure
    df = pd.json_normalize(
        data,
        sep="_",
        record_path=None,  # No specific record path; flatten everything
        meta=[
            "host",
            ["op", "command"],
            ["op", "guard"],
            ["op", "host_info", "host"],
            ["op", "host_info", "username"],
            ["op", "host_info", "password"],
            ["op", "global_info", "sudo_user"],
            ["op", "global_info", "sudo_password"],
            ["cp", "env"],
            ["cp", "command"],
            ["cp", "subsystem"],
            ["cp", "exit_status"],
            ["cp", "exit_signal"],
            ["cp", "returncode"],
            ["cp", "stdout"],
            ["cp", "stderr"],
            "changed",
            "executed",
            "error"
        ]
    )

    # Debugging: Print the flattened DataFrame columns
    print("DataFrame columns:", df.columns)

    # Step 2: Extract unique hosts
    try:
        hosts = sorted(df['host'].unique())
    except KeyError as e:
        raise KeyError("Error: The 'data' variable must contain a 'host' key.") from e

    # Step 3: Ensure 'op_command' exists in the DataFrame
    if 'op_command' not in df.columns:
        raise KeyError("Error: The 'op_command' field is missing or incorrectly nested in the input data.")

    # Step 4: Replace dots in hostnames
    df['safe_host'] = df['host'].str.replace(".", "_")

    # Step 5: Create additional columns for executed and changed
    df['executed'] = df['cp_returncode']
    df['changed'] = df['cp_stdout'].apply(lambda x: str(x)[:60] if x is not None else "None")

    # Step 6: Pivot the data
    pivot_executed = df.pivot(index='op_command', columns='safe_host', values='executed').fillna("")
    pivot_changed = df.pivot(index='op_command', columns='safe_host', values='changed').fillna("")

    # Combine executed and changed columns
    combined = pd.concat([pivot_executed, pivot_changed], axis=1, keys=['Executed', 'Changed'])
    combined.columns = [f"{host}_{key}" for key, host in combined.columns]

    # Add the 'Command' column back
    combined.reset_index(inplace=True)
    combined.rename(columns={'op_command': 'Command'}, inplace=True)

    # Step 7: Generate column definitions
    column_defs = [{"headerName": "Command", "field": "Command"}]
    for host in hosts:
        safe_host = host.replace(".", "_")
        column_defs.append({"headerName": f"{host} Returncode", "field": f"{safe_host}_Executed"})
        column_defs.append({"headerName": f"{host} Stdout", "field": f"{safe_host}_Changed"})

    return column_defs, combined

from tabulate import tabulate

def generate_table(data):
    """
    Converts command output data into a tabular format suitable for display.

    Args:
        data (list[dict]): A list of dictionaries representing command executions.

    Returns:
        str: A formatted table string.
    """
    column_defs, df = _generate_table(data)

    # Step 1: Extract headers from column definitions
    headers = [col['headerName'] for col in column_defs]

    # Step 2: Format rows
    table_data = df.to_dict(orient='records')

    # Step 3: Generate and return the table
    return tabulate(table_data, headers=headers, tablefmt="grid")

def generate_grid(data):
    """
    Generates column definitions and row data for a grid.

    Args:
        data (list[dict]): A list of dictionaries representing command executions.

    Returns:
        tuple: A tuple containing:
            - columnDefs (list[dict]): Definitions for grid columns.
            - rowData (pd.DataFrame): The processed data as a DataFrame.
    """
    column_defs, df = _generate_table(data)
    return column_defs, df.to_dict(orient='records')