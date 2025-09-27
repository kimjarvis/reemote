import json
import pandas as pd

def convert_to_df(data, columns=None):
    """Convert JSON data to DataFrame with specified columns"""
    # If data is a string, parse it as JSON
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse JSON string: {e}")

    # Define all available columns with their extraction logic
    # Added safe handling for None values in cp and op
    all_columns = {
        'command': lambda item: item['op']['command'] if item.get('op') else '',
        'host': lambda item: item['host'],
        'guard': lambda item: item['op']['guard'] if item.get('op') else '',
        'changed': lambda item: item['changed'],
        'executed': lambda item: item['executed'],
        'stdout': lambda item: item['cp']['stdout'] if item.get('cp') else '',
        'stderr': lambda item: item['cp']['stderr'] if item.get('cp') else '',
        'exit_status': lambda item: item['cp']['exit_status'] if item.get('cp') else '',
        'returncode': lambda item: item['cp']['returncode'] if item.get('cp') else '',
        'env': lambda item: item['cp'].get('env', '') if item.get('cp') else '',
        'subsystem': lambda item: item['cp'].get('subsystem', '') if item.get('cp') else '',
        'exit_signal': lambda item: item['cp'].get('exit_signal', '') if item.get('cp') else '',
        'error': lambda item: item.get('error', '')
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