import tabulate as tabulate
def safe_convert(value):
    """Convert any value to a safe string representation"""
    if value is None:
        return ''
    elif isinstance(value, bool):
        return 'True' if value else 'False'
    else:
        return str(value)


def convert_to_tabulate(df):
    """Convert DataFrame to tabulate format"""
    # Create a new DataFrame with all values converted to strings
    df_display = df.copy()
    for col in df_display.columns:
        df_display[col] = df_display[col].apply(safe_convert)

    # Convert DataFrame to a format suitable for tabulate
    table_data = df_display.values.tolist()
    headers = df_display.columns.tolist()

    return tabulate.tabulate(table_data, headers=headers, tablefmt='grid')
