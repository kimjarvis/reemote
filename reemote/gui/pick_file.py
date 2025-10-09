from nicegui import ui

from reemote.execute import execute
from reemote.gui.local_file_picker import local_file_picker
from reemote.operations.sftp.write_file import Write_file
from reemote.utilities.produce_grid import produce_grid
from reemote.utilities.produce_json import produce_json
from reemote.utilities.produce_output_grid import produce_output_grid


async def pick_file(inv, fp, sr, er) -> None:
    result = await local_file_picker('~', multiple=False)
    ui.notify(f'Uploading file {result}')
    with open(result[0], 'r', encoding='utf-8') as file:
        text = file.read()
    responses = await execute(inv.inventory,Write_file(path=fp.path,text=text))
    c, r = produce_grid(produce_json(responses))
    er.set(c, r)
    er.execution_report.refresh()
    c, r = produce_output_grid(produce_json(responses))
    sr.set(c, r)
    sr.execution_report.refresh()
