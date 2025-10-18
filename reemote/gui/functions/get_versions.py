# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.execute import execute
from reemote.utilities.produce_grid import produce_grid
from reemote.utilities.produce_json import produce_json
from reemote.utilities.produce_output_grid import produce_output_grid


async def get_versions(inventory, versions, manager, stdout_report, execution_report):
    if manager.manager=='apk':
        from reemote.facts.apk.get_packages import Get_packages
        responses = await execute(inventory.inventory, Get_packages())
        versions.get_versions(responses)
        versions.version_report.refresh()
    if manager.manager=='pip':
        from reemote.facts.pip.get_packages import Get_packages
        responses = await execute(inventory.inventory, Get_packages())
        versions.get_versions(responses)
        versions.version_report.refresh()
    if manager.manager=='apt':
        from reemote.facts.apt.get_packages import Get_packages
        responses = await execute(inventory.inventory, Get_packages())
        versions.get_versions(responses)
        versions.version_report.refresh()
    if manager.manager=='dpkg':
        from reemote.facts.dpkg.get_packages import Get_packages
        responses = await execute(inventory.inventory, Get_packages())
        versions.get_versions(responses)
        versions.version_report.refresh()
    if manager.manager=='dnf':
        from reemote.facts.dnf.get_packages import Get_packages
        responses = await execute(inventory.inventory, Get_packages())
        versions.get_versions(responses)
        versions.version_report.refresh()
    if manager.manager=='yum':
        from reemote.facts.yum.get_packages import Get_packages
        responses = await execute(inventory.inventory, Get_packages())
        versions.get_versions(responses)
        versions.version_report.refresh()

    columns, rows = produce_grid(produce_json(responses))
    execution_report.set(columns, rows)
    execution_report.execution_report.refresh()
    columns, rows = produce_output_grid(produce_json(responses))
    stdout_report.set(columns, rows)
    stdout_report.execution_report.refresh()
