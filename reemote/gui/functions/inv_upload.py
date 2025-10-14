# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.utilities.produce_grid import produce_grid
from reemote.utilities.produce_json import produce_json
from reemote.utilities.produce_output_grid import produce_output_grid


async def upload_inventory(inventory, execution_report, stdout_report):
    # Start with the fixed column definition for "Command"
    columns = [{'headerName': 'Command', 'field': 'command'}]
    rows = []

    # Dynamically generate column definitions for each host
    for index, (host_info, _) in enumerate(inventory.inventory):
        host_ip = host_info['host']
        columns.append({'headerName': f'{host_ip} Executed', 'field': f'{host_ip.replace(".","_")}_executed'})
        columns.append({'headerName': f'{host_ip} Changed', 'field': f'{host_ip.replace(".","_")}_changed'})

    # columns, rows = produce_grid(produce_json(responses))
    execution_report.set(columns, rows)
    execution_report.execution_report.refresh()
    # columns, rows = produce_output_grid(produce_json(responses))
    stdout_report.set(columns, rows)
    stdout_report.execution_report.refresh()
