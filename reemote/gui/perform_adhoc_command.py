from reemote.execute import execute
from reemote.operations.server.shell import Shell
from reemote.utilities.produce_grid import produce_grid
from reemote.utilities.produce_json import produce_json
from reemote.utilities.produce_output_grid import produce_output_grid


async def perform_adhoc_command(inv, sr, er, ah):
    responses = await execute(inv.inventory,
                              Shell(cmd=ah.command, su=ah.su, sudo=ah.sudo))
    c, r = produce_grid(produce_json(responses))
    er.set(c, r)
    er.execution_report.refresh()
    c, r = produce_output_grid(produce_json(responses))
    sr.set(c, r)
    sr.execution_report.refresh()
