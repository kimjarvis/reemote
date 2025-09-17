import re

from nicegui import ui, native, app
from reemote.gui import Gui
from reemote.execute import execute
from reemote.produce_grid import produce_grid
from reemote.produce_json import produce_json
from reemote.operations.server.shell import Shell
from reemote.operations.apk.packages import Packages


class Version_grid:
    def __init__(self):
        app.storage.user["columnDefs1"] = []
        app.storage.user["rowData1"] = []

    def split_package_name_version(self,pkg_str):
        import re
        # Match from end: <version>-r<number>
        # We want to split at the LAST hyphen before "-r<number>"
        parts = pkg_str.rsplit('-', 2)  # Split into at most 3 parts, from the right
        if len(parts) == 3 and re.match(r'^r\d+$', parts[2]):
            name = parts[0]
            version = parts[1] + '-' + parts[2]
            return name, version
        else:
            # Fallback: if format doesn't match, return whole string as name, empty version
            return pkg_str, ""

    async def List_package_versions(self, responses):
        # Parse packages for each host
        host_packages = []
        host_names = []

        for i, r in enumerate(responses):
            host_name = r.host
            host_names.append(host_name)
            pkgs = r.cp.stdout.splitlines()
            pkg_dict = {}
            for pkg in pkgs:
                name, version = self.split_package_name_version(pkg)
                pkg_dict[name] = version
            host_packages.append(pkg_dict)

        # Get all unique package names across all hosts
        all_package_names = set()
        for pkg_dict in host_packages:
            all_package_names.update(pkg_dict.keys())
        all_package_names = sorted(all_package_names)

        # Build column definitions: Name + one per host
        columnDefs = [{"headerName": "Package Name", "field": "name"}]
        for host_name in host_names:
            columnDefs.append({"headerName": host_name, "field": host_name.replace(".","_")})

        # Build row data
        rowData = []
        for pkg_name in all_package_names:
            row = {"name": pkg_name}
            for i, host_name in enumerate(host_names):
                row[host_name.replace(".","_")] = host_packages[i].get(pkg_name, "")  # empty if not installed
            rowData.append(row)

        # Store in app.storage
        app.storage.user["columnDefs1"] = columnDefs
        app.storage.user["rowData1"] = rowData
        self.version_report.refresh()

    @ui.refreshable
    def version_report(self):
        return ui.aggrid({
            'columnDefs': app.storage.user["columnDefs1"],
            'rowData': app.storage.user["rowData1"],
        }).classes('max-h-80  overflow-y-auto')

async def get_versions(gui, version_grid):
    responses = await execute(app.storage.user["inventory"],Shell("apk info -v"))
    await version_grid.List_package_versions(responses)
    app.storage.user["columnDefs"],app.storage.user["rowData"] = produce_grid(produce_json(responses))
    gui.execution_report.refresh()
    version_grid.version_report.refresh()

async def install(gui, version_grid):
    pkg=app.storage.user["package"]
    responses = await execute(app.storage.user["inventory"],Packages(packages=[pkg],present=True, su=True))
    app.storage.user["columnDefs"],app.storage.user["rowData"] = produce_grid(produce_json(responses))
    gui.execution_report.refresh()
    version_grid.version_report.refresh()

async def remove(gui, version_grid):
    pkg=app.storage.user["package"]
    responses = await execute(app.storage.user["inventory"],Packages(packages=[pkg],present=False, su=True))
    app.storage.user["columnDefs"],app.storage.user["rowData"] = produce_grid(produce_json(responses))
    gui.execution_report.refresh()
    version_grid.version_report.refresh()

@ui.page('/')
def page():
    gui = Gui()
    version_grid = Version_grid()
    gui.upload_inventory()
    ui.button('Show installed packages', on_click=lambda: get_versions(gui, version_grid))
    ui.input(label='Package').bind_value(app.storage.user, 'package')
    ui.button('Add package', on_click=lambda: install(gui, version_grid))
    ui.button('Remove package', on_click=lambda: remove(gui, version_grid))
    version_grid.version_report()
    gui.execution_report()


ui.run(title="Package versions", reload=False, port=native.find_open_port(),
       storage_secret='private key to secure the browser session cookie')
