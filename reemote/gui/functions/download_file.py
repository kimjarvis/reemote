from reemote.execute import execute
from reemote.operations.scp.download import Download
from reemote.utilities.produce_grid import produce_grid
from reemote.utilities.produce_json import produce_json
from reemote.utilities.produce_output_grid import produce_output_grid


async def download_file(inventory, file_path, stdout_report, execution_report):
    responses = await execute(inventory.inventory, Download(srcpaths=file_path.path, dstpath="~/Downloads/file.txt")) # [0][0]['host']

    columns, rows = produce_grid(produce_json(responses))
    execution_report.set(columns, rows)
    execution_report.execution_report.refresh()
    columns, rows = produce_output_grid(produce_json(responses))
    stdout_report.set(columns, rows)
    stdout_report.execution_report.refresh()
