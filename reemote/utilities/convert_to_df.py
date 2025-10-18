# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import json
import pandas as pd

def convert_to_df(data, columns=None):
    """Converts structured JSON data into a pandas DataFrame.

    This module provides the `convert_to_df` function, which takes structured
    data (as a JSON string or list of dictionaries) and transforms it into a
    pandas DataFrame. It is designed to flatten a nested JSON structure, often
    from command-line tool outputs, based on a predefined set of extraction rules.

    The main function, `convert_to_df`, performs several key transformations:

    - Parses the input if it is a JSON-formatted string.
    - Extracts data from nested objects (e.g., 'op', 'cp') into top-level columns.
    - Gracefully handles missing keys or objects by using empty strings as
      fallback values.
    - Allows the user to select a custom subset of columns for the final DataFrame.
    - Validates requested column names against the list of available fields.

    The function signature is:
        convert_to_df(data, columns=None)

    Args:
        data (str or list[dict]): The input data. This can be a JSON-formatted
            string or a list of dictionaries representing the rows.
        columns (list[str], optional): A list of column names to be included
            in the output DataFrame. If None, all predefined columns will be
            used. Defaults to None.

    Returns:
        pd.DataFrame: A pandas DataFrame containing the extracted and
            flattened data, with columns ordered as specified.

    Raises:
        ValueError: If the input `data` is a string that cannot be parsed as
            JSON, or if a requested column name in the `columns` list is not
            available.
    """

    # If data is a string, parse it as JSON
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse JSON string: {e}")

    # Define all available columns with their extraction logic
    all_columns = {
        'command': lambda item: item['op']['command'] if isinstance(item, dict) and item.get('op') else '',
        'host': lambda item: item['host'] if isinstance(item, dict) else '',
        'guard': lambda item: item['op']['guard'] if isinstance(item, dict) and item.get('op') else '',
        'changed': lambda item: item['changed'] if isinstance(item, dict) else '',
        'executed': lambda item: item['executed'] if isinstance(item, dict) else '',
        'stdout': lambda item: item['cp']['stdout'] if isinstance(item, dict) and item.get('cp') else '',
        'stderr': lambda item: item['cp']['stderr'] if isinstance(item, dict) and item.get('cp') else '',
        'exit_status': lambda item: item['cp']['exit_status'] if isinstance(item, dict) and item.get('cp') else '',
        'returncode': lambda item: item['cp']['returncode'] if isinstance(item, dict) and item.get('cp') else '',
        'env': lambda item: item['cp'].get('env', '') if isinstance(item, dict) and item.get('cp') else '',
        'subsystem': lambda item: item['cp'].get('subsystem', '') if isinstance(item, dict) and item.get('cp') else '',
        'exit_signal': lambda item: item['cp'].get('exit_signal', '') if isinstance(item, dict) and item.get('cp') else '',
        'error': lambda item: item.get('error', '') if isinstance(item, dict) else ''
    }

    # If no columns specified, use all columns
    if columns is None:
        columns = list(all_columns.keys())

    # Validate that all requested columns exist
    for col in columns:
        if col not in all_columns:
            raise ValueError(f"Column '{col}' is not available. Available columns: {list(all_columns.keys())}")

    rows = []
    for item in data:
        # Skip items that are not dictionaries
        if not isinstance(item, dict):
            print(f"Warning: Skipping non-dictionary item: {item}")
            continue

        row = {}
        for col in columns:
            try:
                row[col] = all_columns[col](item)
            except (KeyError, TypeError) as e:
                # Fallback to empty string if there's an error extracting the value
                row[col] = ''
                print(f"Warning: Could not extract column '{col}' from item: {e}")
        rows.append(row)

    # Create DataFrame with only the specified columns
    return pd.DataFrame(rows, columns=columns)