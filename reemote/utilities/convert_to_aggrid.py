def convert_to_aggrid(df):
    """Convert DataFrame to AgGrid format (columnDefs, rowData)"""
    # Create column definitions
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
