# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
def convert_to_aggrid(df):
    """
    Converts a pandas DataFrame into a format suitable for AG-Grid.

    This function takes a pandas DataFrame and processes it to generate
    the necessary `columnDefs` and `rowData` for use with the AG-Grid
    library. It automatically creates column definitions by inspecting
    the DataFrame's columns and data types.

    Key transformations include:

    - Generating human-readable 'headerName' from column names.
    - Enabling sorting, filtering, and resizing for all columns.
    - Assigning a 'numericColumn' type for integer and float columns.
    - Using an 'agCheckboxCellRenderer' for boolean columns.
    - Converting the DataFrame records into a JSON-serializable list of
      dictionaries, replacing NaN values with None.

    Args:
        df (pd.DataFrame): The input pandas DataFrame to be converted.

    Returns:
        tuple: A tuple containing two elements:

            - columnDefs (list[dict]): A list of dictionaries defining the
              properties for each AG-Grid column.
            - rowData (list[dict]): A list of dictionaries, where each
              dictionary represents a row of data from the DataFrame.
    """
    columnDefs = []
    for column in df.columns:
        col_def = {
            'headerName': column.replace('_', ' ').title(),
            'field': column,
            'sortable': True,
            'filter': True,
            'resizable': True
        }

        # Add specific column properties based on data type
        if df[column].dtype in ['int64', 'float64']:
            col_def['type'] = 'numericColumn'
        elif df[column].dtype == 'bool':
            col_def['cellRenderer'] = 'agCheckboxCellRenderer'

        columnDefs.append(col_def)

    # Convert DataFrame to row data
    rowData = df.replace({float('nan'): None}).to_dict('records')

    return columnDefs, rowData
