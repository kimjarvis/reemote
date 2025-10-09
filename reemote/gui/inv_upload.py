async def inv_upload(inv, er, stdout, sources):
    # Start with the fixed column definition for "Command"
    columns = [{'headerName': 'Command', 'field': 'command'}]
    rows = []

    # Dynamically generate column definitions for each host
    for index, (host_info, _) in enumerate(inv.inventory):
        host_ip = host_info['host']
        columns.append({'headerName': f'{host_ip} Executed', 'field': f'{host_ip.replace(".","_")}_executed'})
        columns.append({'headerName': f'{host_ip} Changed', 'field': f'{host_ip.replace(".","_")}_changed'})
    # print(columns)
    er.set(columns, rows)
    er.execution_report.refresh()
    stdout.set(columns, rows)
    stdout.execution_report.refresh()
