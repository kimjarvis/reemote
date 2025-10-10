from nicegui import ui

from reemote.execute import execute
from reemote.gui.local_file_picker import local_file_picker
from reemote.operations.sftp.write_file import Write_file
from reemote.utilities.produce_grid import produce_grid
from reemote.utilities.produce_json import produce_json
from reemote.utilities.produce_output_grid import produce_output_grid


async def pick_file(inventory, file_path, stdout_report, execution_report) -> None:
    result = await local_file_picker('~', multiple=False)
    ui.notify(f'Uploading file {result}')
    with open(result[0], 'r', encoding='utf-8') as file:
        text = file.read()
    responses = await execute(inventory.inventory, Write_file(path=file_path.path, text=text))

    columns, rows = produce_grid(produce_json(responses))
    execution_report.set(columns, rows)
    execution_report.execution_report.refresh()
    columns, rows = produce_output_grid(produce_json(responses))
    stdout_report.set(columns, rows)
    stdout_report.execution_report.refresh()
