from reemote.execute import execute
from reemote.utilities.produce_grid import produce_grid
from reemote.utilities.produce_json import produce_json


async def install(inv, versions, manager, sr, er):
    pkg=manager.package
    if manager.manager=='apk':
        from reemote.operations.apk.packages import Packages
        responses = await execute(inv.inventory, Packages(packages=[pkg], present=True, su=manager.su, sudo=manager.sudo))
    if manager.manager=='pip':
        from reemote.operations.pip.packages import Packages
        responses = await execute(inv.inventory, Packages(packages=[pkg], present=True, su=manager.su, sudo=manager.sudo))
    if manager.manager=='apt':
        from reemote.operations.apt.packages import Packages
        responses = await execute(inv.inventory, Packages(packages=[pkg], present=True, su=manager.su, sudo=manager.sudo))
    if manager.manager=='dpkg':
        from reemote.operations.dpkg.packages import Packages
        responses = await execute(inv.inventory, Packages(packages=[pkg], present=True, su=manager.su, sudo=manager.sudo))
    if manager.manager=='dnf':
        from reemote.operations.dnf.packages import Packages
        responses = await execute(inv.inventory, Packages(packages=[pkg], present=True, su=manager.su, sudo=manager.sudo))
    if manager.manager=='yum':
        from reemote.operations.yum.packages import Packages
        responses = await execute(inv.inventory, Packages(packages=[pkg], present=True, su=manager.su, sudo=manager.sudo))
    c, r = produce_grid(produce_json(responses))
    er.set(c, r)
    er.execution_report.refresh()
    # c, r = produce_output_grid(produce_json(responses))
    # sr.set(c, r)
    # sr.execution_report.refresh()
