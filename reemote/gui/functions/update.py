# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.execute import execute
from reemote.utilities.produce_grid import produce_grid
from reemote.utilities.produce_json import produce_json
from reemote.utilities.produce_output_grid import produce_output_grid


async def update(inventory, versions, manager, stdout_report, execution_report):
    pkg=manager.package
    if manager.manager=='apk':
        from reemote.operations.apk.update import Update
        responses = await execute(inventory.inventory, Update(su=manager.su, sudo=manager.sudo))
    if manager.manager=='pip':
        from reemote.operations.pip.update import Update
        responses = await execute(inventory.inventory, Update(su=manager.su, sudo=manager.sudo))
    if manager.manager=='apt':
        from reemote.operations.apt.update import Update
        responses = await execute(inventory.inventory, Update(su=manager.su, sudo=manager.sudo))
    if manager.manager=='dpkg':
        from reemote.operations.dpkg.update import Update
        responses = await execute(inventory.inventory, Update(su=manager.su, sudo=manager.sudo))
    if manager.manager=='dnf':
        from reemote.operations.dnf.update import Update
        responses = await execute(inventory.inventory, Update(su=manager.su, sudo=manager.sudo))
    if manager.manager=='yum':
        from reemote.operations.yum.update import Update
        responses = await execute(inventory.inventory, Update(su=manager.su, sudo=manager.sudo))

    columns, rows = produce_grid(produce_json(responses))
    execution_report.set(columns, rows)
    execution_report.execution_report.refresh()
    columns, rows = produce_output_grid(produce_json(responses))
    stdout_report.set(columns, rows)
    stdout_report.execution_report.refresh()
