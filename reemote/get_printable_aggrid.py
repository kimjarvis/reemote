from tabulate import tabulate


def get_printable_aggrid(data):
    """
    Formats and prints the given AGGrid data as a formatted grid.

    Parameters:
        data (dict): A dictionary containing 'columnDefs' and 'rowData'.
                     - 'columnDefs': List of dictionaries with 'headerName' and 'field'.
                     - 'rowData': List of dictionaries representing rows of data.

    Returns:
        None: Prints the formatted grid.
    """
    # Step 1: Extract headers and field mappings
    headers = [col['headerName'] for col in data['columnDefs']]
    field_mapping = {col['field']: col['headerName'] for col in data['columnDefs']}

    # Step 2: Prepare the table data
    table_data = []
    for row in data['rowData']:
        formatted_row = [row[field] for field in field_mapping.keys()]
        table_data.append(formatted_row)

    # Step 3: Print the formatted grid using tabulate
    print(tabulate(table_data, headers=headers, tablefmt="grid"))
