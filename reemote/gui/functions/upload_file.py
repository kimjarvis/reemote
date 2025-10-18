# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from nicegui import ui

from reemote.execute import execute
from reemote.gui.local_file_picker import local_file_picker
from reemote.operations.scp.upload import Upload
from reemote.utilities.produce_grid import produce_grid
from reemote.utilities.produce_json import produce_json
from reemote.utilities.produce_output_grid import produce_output_grid


async def upload_file(fp, inventory, file_path, stdout_report, execution_report) -> None:

    print("upload",fp.paths)
    print("to", file_path.path)
    if file_path.path=="":
        file_path.path="/home/kim"

    responses=[]
    for path in fp.paths:
        r = await execute(inventory.inventory, Upload(srcpaths=path, dstpath=file_path.path))
        responses.extend(r)

    columns, rows = produce_grid(produce_json(responses))
    execution_report.set(columns, rows)
    execution_report.execution_report.refresh()
    columns, rows = produce_output_grid(produce_json(responses))
    stdout_report.set(columns, rows)
    stdout_report.execution_report.refresh()
