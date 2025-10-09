from reemote.execute import execute
from reemote.utilities.produce_grid import produce_grid
from reemote.utilities.produce_json import produce_json
from reemote.utilities.produce_output_grid import produce_output_grid


async def get_versions(inv, versions, manager, sr, er):
    if manager.manager=='apk':
        from reemote.facts.apk.get_packages import Get_packages
        responses = await execute(inv.inventory,Get_packages())
        versions.get_versions(responses)
        versions.version_report.refresh()
    if manager.manager=='pip':
        from reemote.facts.pip.get_packages import Get_packages
        responses = await execute(inv.inventory,Get_packages())
        versions.get_versions(responses)
        versions.version_report.refresh()
    if manager.manager=='apt':
        from reemote.facts.apt.get_packages import Get_packages
        responses = await execute(inv.inventory, Get_packages())
        versions.get_versions(responses)
        versions.version_report.refresh()
    if manager.manager=='dpkg':
        from reemote.facts.dpkg.get_packages import Get_packages
        responses = await execute(inv.inventory, Get_packages())
        versions.get_versions(responses)
        versions.version_report.refresh()
    if manager.manager=='dnf':
        from reemote.facts.dnf.get_packages import Get_packages
        responses = await execute(inv.inventory, Get_packages())
        versions.get_versions(responses)
        versions.version_report.refresh()
    if manager.manager=='yum':
        from reemote.facts.yum.get_packages import Get_packages
        responses = await execute(inv.inventory, Get_packages())
        versions.get_versions(responses)
        versions.version_report.refresh()
    c, r = produce_grid(produce_json(responses))
    er.set(c, r)
    er.execution_report.refresh()
    c, r = produce_output_grid(produce_json(responses))
    sr.set(c, r)
    sr.execution_report.refresh()
    sr.execution_report.refresh()
    er.execution_report.refresh()
