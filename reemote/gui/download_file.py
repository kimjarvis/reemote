from reemote.execute import execute
from reemote.operations.sftp.read_file import Read_file
from reemote.utilities.produce_grid import produce_grid
from reemote.utilities.produce_json import produce_json
from reemote.utilities.produce_output_grid import produce_output_grid


async def download_file(inv, fp, sr, er):
    responses = await execute(inv.inventory,Read_file(path=fp.path)) # [0][0]['host']
    c, r = produce_grid(produce_json(responses))
    er.set(c, r)
    er.execution_report.refresh()
    c, r = produce_output_grid(produce_json(responses))
    sr.set(c, r)
    sr.execution_report.refresh()
