# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import tabulate as tabulate



def safe_convert(value):
    """Converts any value to a safe string representation for display.

    This helper function is designed to handle different data types before
    they are rendered in a text-based table. It ensures that all values,
    regardless of their original type, are represented as strings in a
    consistent and readable manner.

    Specific conversions are:

    - None is converted to an empty string ('').
    - Boolean values are converted to their string counterparts ('True' or 'False').
    - All other types are converted using the standard str() function.

    Args:
        value (Any): The input value to be converted.

    Returns:
        str: A string representation of the input value, suitable for display.
    """
    if value is None:
        return ''
    elif isinstance(value, bool):
        return 'True' if value else 'False'
    else:
        return str(value)


def convert_to_tabulate(df):
    """Converts a pandas DataFrame into a grid-style formatted string.

    This function takes a pandas DataFrame and transforms it into a
    well-formatted, human-readable text table using the `tabulate` library.
    It is particularly useful for printing DataFrames to a console or log builtin.

    The process involves:

    - Creating a deep copy of the DataFrame to prevent side effects.
    - Safely converting all cell values to strings using the `safe_convert`
      helper function. This handles `None`, booleans, and other types
      to ensure compatibility with `tabulate`.
    - Extracting the column headers and the row data.
    - Generating a string table with a 'grid' format.

    Args:
        df (pd.DataFrame): The input pandas DataFrame to be formatted.

    Returns:
        str: A single string representing the DataFrame as a formatted
             grid table.
    """
    # Create a new DataFrame with all values converted to strings
    df_display = df.copy()
    for col in df_display.columns:
        df_display[col] = df_display[col].apply(safe_convert)

    # Convert DataFrame to a format suitable for tabulate
    table_data = df_display.values.tolist()
    headers = df_display.columns.tolist()

    return tabulate.tabulate(table_data, headers=headers, tablefmt='grid')
