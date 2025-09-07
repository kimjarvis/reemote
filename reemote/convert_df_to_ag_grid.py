def convert_df_to_ag_grid(df):
    # Reset index to make the index a column
    df_reset = df.reset_index()

    # Convert all column names to strings
    df_reset.columns = [str(col) if not isinstance(col, tuple) else ' '.join(map(str, col)).strip()
                        for col in df_reset.columns]

    # Create column definitions
    column_defs = []
    for col in df_reset.columns:
        field_name = col.lower().replace(' ', '_')
        column_defs.append({'headerName': col, 'field': field_name})

    # Create row data
    row_data = []
    for index, row in df_reset.iterrows():
        row_dict = {}
        for col in df_reset.columns:
            field_name = col.lower().replace(' ', '_')
            row_dict[field_name] = row[col]
        row_data.append(row_dict)

    return {
        'columnDefs': column_defs,
        'rowData': row_data
    }
