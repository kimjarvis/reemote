import sys
from nicegui import native
from reemote.execute import execute
from reemote.utilities.produce_grid import produce_grid
from reemote.utilities.produce_output_grid import produce_output_grid
from reemote.utilities.produce_json import produce_json
from reemote.utilities.verify_source_file_contains_valid_class import verify_source_file_contains_valid_class
from reemote.utilities.validate_root_class_name_and_get_root_class import validate_root_class_name_and_get_root_class
from reemote.gui.local_file_picker import local_file_picker
from reemote.utilities.get_classes_in_source import get_classes_in_source
from reemote.operations.server.shell import Shell
from reemote.operations.sftp.read_file import Read_file
from reemote.operations.sftp.write_file import Write_file
from reemote.utilities.parse_kwargs_string import parse_kwargs_string


from nicegui import events, ui

from reemote.utilities.validate_inventory_structure import validate_inventory_structure
from reemote.utilities.verify_inventory_connect import verify_inventory_connect

class Execution_report:
    def __init__(self):
        self.columns = [{'headerName': 'Command', 'field': 'command'}]
        self.rows = []

    def set(self, columns, rows):
        self.columns = columns
        self.rows = rows

    @ui.refreshable
    def execution_report(self):
        return ui.label("Execution Report"),ui.aggrid({
            'columnDefs': self.columns,
            'rowData': self.rows,
        }).classes('max-h-40  overflow-y-auto')

class Inventory_upload:
    def __init__(self):
        self.inventory = None

    async def handle_upload(self, e: events.UploadEventArguments):
        text = e.content.read().decode('utf-8')
        exec(text, globals())
        if not validate_inventory_structure(inventory()):
            ui.notify("Inventory structure is invalid")
            return
        if not await verify_inventory_connect(inventory()):
            ui.notify("Inventory connections are invalid")
            return
        ui.notify("Inventory structure and all hosts connect")
        self.inventory = inventory()

    # def upload_inventory(self):
    #     return ui.upload(label="UPLOAD INVENTORY",
    #          on_upload=self.handle_upload,  # Handle the file upload
    #     ).props('accept=.py').classes('max-w-full')


class Stdout_report:
    def __init__(self):
        self.columns = [{'headerName': 'Command', 'field': 'command'}]
        self.rows = []

    def set(self, columns, rows):
        self.columns = columns
        self.rows = rows

    @ui.refreshable
    def execution_report(self):
        return ui.label("Execution Output"),ui.aggrid({
            'columnDefs': self.columns,
            'rowData': self.rows,
        }).classes('max-h-40  overflow-y-auto')

class Sources_upload:
    def __init__(self):
        self.source= "/"
        self._classes = []
        self.deployment = ""
        self.kwargs = ""

    @ui.refreshable
    def classes(self):
        return ui.select(self._classes).bind_value(self, 'deployment')

    def _kwargs(self):
        return ui.input(label='kwargs', placeholder='kwargs string').bind_value(self, 'kwargs')

    async def pick_file(self) -> None:
        result = await local_file_picker('~', multiple=False)
        ui.notify(f'Uploading file {result}')
        self.source = result[0]
        self._classes = get_classes_in_source(result[0])
        self.classes.refresh()

class Wrapper:

    def __init__(self, command):
        self.command = command

    def execute(self):
        # Execute a shell command on all hosts
        r = yield self.command()
        # The result is available in stdout
        # print(r.cp.stdout)

async def inv_upload(inv, er, stdout, sources):
    # Start with the fixed column definition for "Command"
    columns = [{'headerName': 'Command', 'field': 'command'}]
    rows = []

    # Dynamically generate column definitions for each host
    for index, (host_info, _) in enumerate(inv.inventory):
        host_ip = host_info['host']
        columns.append({'headerName': f'{host_ip} Executed', 'field': f'{host_ip.replace(".","_")}_executed'})
        columns.append({'headerName': f'{host_ip} Changed', 'field': f'{host_ip.replace(".","_")}_changed'})
    # print(columns)
    er.set(columns, rows)
    er.execution_report.refresh()
    stdout.set(columns, rows)
    stdout.execution_report.refresh()


async def run_the_deploy(inv, er, stdout, sources):
    if sources.source != "/":
        if sources.source and sources.deployment:
            if not verify_source_file_contains_valid_class(sources.source, sources.deployment):
                sys.exit(1)

        # Verify the source and class
        if sources.source and sources.deployment:
            root_class = validate_root_class_name_and_get_root_class(sources.deployment, sources.source)

        if not root_class:
            print("root class not found")
            sys.exit(1)

        # Parse parameters into kwargs
        kwargs = parse_kwargs_string(sources.kwargs)
        responses = []
        responses = await execute(inventory(), root_class(**kwargs))
        # responses = await execute(inv.inventory, Wrapper(root_class))
        # responses = await execute(inv.inventory, root_class(**sources.kwargs))
        c, r =produce_grid(produce_json(responses))
        er.set(c, r)
        er.execution_report.refresh()
        c, r =produce_output_grid(produce_json(responses))
        stdout.set(c, r)
        stdout.execution_report.refresh()



class Ad_Hoc:
    def __init__(self):
        self.sudo = False
        self.su = False
        self.command = ""



async def Perform_adhoc_command(inv, sr, er, ah):
    responses = await execute(inv.inventory,
                              Shell(cmd=ah.command, su=ah.su, sudo=ah.sudo))
    c, r = produce_grid(produce_json(responses))
    er.set(c, r)
    er.execution_report.refresh()
    c, r = produce_output_grid(produce_json(responses))
    sr.set(c, r)
    sr.execution_report.refresh()


class File_path:
    def __init__(self):
        self.path = ""

async def Download_file(inv, fp, sr, er):
    responses = await execute(inv.inventory,Read_file(path=fp.path)) # [0][0]['host']
    c, r = produce_grid(produce_json(responses))
    er.set(c, r)
    er.execution_report.refresh()
    c, r = produce_output_grid(produce_json(responses))
    sr.set(c, r)
    sr.execution_report.refresh()

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


class Versions:
    def __init__(self):
        self.columns = []
        self.rows = []

    def get_versions(self, responses):
        host_packages = []
        host_names = []

        for i, r in enumerate(responses):
            # print(r.cp.stdout)
            host_name = r.host
            host_names.append(host_name)
            pkg_dict = {}
            for v in r.cp.stdout:
                pkg_dict[v["name"]] = v["version"]
            host_packages.append(pkg_dict)

        # print(host_packages)
        # print(host_names)

        # Get all unique package names across all hosts
        all_package_names = set()
        for pkg_dict in host_packages:
            all_package_names.update(pkg_dict.keys())
        all_package_names = sorted(all_package_names)
        # print(all_package_names)

        # Build column definitions: Name + one per host
        columnDefs = [
            {"headerName": "Package Name", "field": "name", 'filter': 'agTextColumnFilter', 'floatingFilter': True}]
        for host_name in host_names:
            columnDefs.append({"headerName": host_name, "field": host_name.replace(".", "_")})

        # Build row data
        rowData = []
        for pkg_name in all_package_names:
            row = {"name": pkg_name}
            for i, host_name in enumerate(host_names):
                row[host_name.replace(".", "_")] = host_packages[i].get(pkg_name, "")  # empty if not installed
            rowData.append(row)

        self.columns = columnDefs
        self.rows = rowData

    @ui.refreshable
    def version_report(self):
        return ui.label("Version Report"),ui.aggrid({
            'columnDefs': self.columns,
            'rowData': self.rows,
        }).classes('max-h-40  overflow-y-auto')

class Manager:
    def __init__(self):
        self.sudo = False
        self.su = False
        self.manager = "apk"
        self.package = ""

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


async def install(inv, versions, manager, sr, er):
    pkg=manager.package
    if manager.manager=='apk':
        from reemote.operations.apk.packages import Packages
        responses = await execute(inv.inventory,Packages(packages=[pkg],present=True, su=manager.su, sudo=manager.sudo))
    if manager.manager=='pip':
        from reemote.operations.pip.packages import Packages
        responses = await execute(inv.inventory,Packages(packages=[pkg],present=True, su=manager.su, sudo=manager.sudo))
    if manager.manager=='apt':
        from reemote.operations.apt.packages import Packages
        responses = await execute(inv.inventory,Packages(packages=[pkg],present=True, su=manager.su, sudo=manager.sudo))
    if manager.manager=='dpkg':
        from reemote.operations.dpkg.packages import Packages
        responses = await execute(inv.inventory,Packages(packages=[pkg],present=True, su=manager.su, sudo=manager.sudo))
    if manager.manager=='dnf':
        from reemote.operations.dnf.packages import Packages
        responses = await execute(inv.inventory,Packages(packages=[pkg],present=True, su=manager.su, sudo=manager.sudo))
    if manager.manager=='yum':
        from reemote.operations.yum.packages import Packages
        responses = await execute(inv.inventory,Packages(packages=[pkg],present=True, su=manager.su, sudo=manager.sudo))
    c, r = produce_grid(produce_json(responses))
    er.set(c, r)
    er.execution_report.refresh()
    c, r = produce_output_grid(produce_json(responses))
    sr.set(c, r)
    sr.execution_report.refresh()
    sr.execution_report.refresh()
    er.execution_report.refresh()

async def remove(inv, versions ,manager, sr, er):
    pkg=manager.package
    if manager.manager=='apk':
        from reemote.operations.apk.packages import Packages
        responses = await execute(inv.inventory,Packages(packages=[pkg],present=False, su=manager.su, sudo=manager.sudo))
    if manager.manager=='pip':
        from reemote.operations.pip.packages import Packages
        responses = await execute(inv.inventory,Packages(packages=[pkg],present=False, su=manager.su, sudo=manager.sudo))
    if manager.manager=='apt':
        from reemote.operations.apt.packages import Packages
        responses = await execute(inv.inventory,Packages(packages=[pkg],present=False, su=manager.su, sudo=manager.sudo))
    if manager.manager=='dpkg':
        from reemote.operations.dpkg.packages import Packages
        responses = await execute(inv.inventory,Packages(packages=[pkg],present=False, su=manager.su, sudo=manager.sudo))
    if manager.manager=='dnf':
        from reemote.operations.dnf.packages import Packages
        responses = await execute(inv.inventory,Packages(packages=[pkg],present=False, su=manager.su, sudo=manager.sudo))
    if manager.manager=='yum':
        from reemote.operations.yum.packages import Packages
        responses = await execute(inv.inventory,Packages(packages=[pkg],present=False, su=manager.su, sudo=manager.sudo))
    c, r = produce_grid(produce_json(responses))
    er.set(c, r)
    er.execution_report.refresh()
    c, r = produce_output_grid(produce_json(responses))
    sr.set(c, r)
    sr.execution_report.refresh()
    sr.execution_report.refresh()
    er.execution_report.refresh()

@ui.page('/')
def page():
    with ui.header().classes(replace='row items-center') as header:
        with ui.tabs() as tabs:
            ui.tab('Inventory')
            ui.tab('Deployment Manager')
            ui.tab('Ad-hoc Commands')
            ui.tab('File Manager')
            ui.tab('Package Manager')

    with ui.tab_panels(tabs, value='Inventory').classes('w-full'):
        with ui.tab_panel('Inventory'):
            sr = Stdout_report()
            er = Execution_report()
            sources = Sources_upload()
            inv = Inventory_upload()

            async def combined_upload_handler(e):
                await inv.handle_upload(e)  # Handle the upload first
                await inv_upload(inv, er, sr, sources)  # Then run your setup logic

            with ui.row():
                ui.upload(
                    label="UPLOAD INVENTORY",
                    on_upload=combined_upload_handler
                ).props('accept=.py').classes('max-w-full')
                ui.markdown("""
                Use the + to upload an inventory file.  
                
                An inventory is a python file that defines an inventory() function, like this:
                
                ```python
                from typing import List, Tuple, Dict, Any

                def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
                     return [
                        (
                            {
                                'host': '10.156.135.16',  # alpine
                                'username': 'user',  # User name
                                'password': 'user'  # Password
                            },
                            {
                                'su_user': 'root',
                                'su_password': 'root'  # Password
                            }
                        )
                    ]
                ```
                It is a list of tuples, each containing two dictionaries. 
                 
                - The first, contains the parameters of Asyncio connect.
                - The second, contains information for su and sudo access and global values.   
                
                The inventory file format is described in detail [here](http://reemote.org/inventory.html).
                """)
            sr.execution_report()
            er.execution_report()

        with ui.tab_panel('Deployment Manager'):
            with ui.row():
                ui.button('Upload Source', on_click=lambda: sources.pick_file(), icon='folder')
                ui.markdown("""
                Use the Upload button to upload a deployment file.  

                A deployment file is python file that contains a list of Python classes, like this:

                ```python
                class Pacman_install_vim:
                    def execute(self):
                        from reemote.operations.pacman.packages import Packages
                        r = yield Packages(packages=["vim"],present=True, sudo=True)
                 
                class Pacman_update:
                    def execute(self):
                        from reemote.operations.pacman.update import Update
                        # Update the packages on all hosts
                        r = yield Update(sudo=True)
                ```
                This file contains two deployments. 

                - The first, Pacman_install_vim, installs vim.
                - The second, Pacman_update, updates all Packman packages.   

                The deployment file format is described in detail [here](http://reemote.org/deployment.html).
                """)

            with ui.row():
                sources.classes()
                ui.markdown("""
                Choose a deployment from the drop down list.  
                """)

            with ui.row():
                sources._kwargs()
                ui.markdown("""
                Input the deployment arguments eg. "present=True".  
                """)

            with ui.row():
                ui.button('Deploy', on_click=lambda: run_the_deploy(inv, er, sr, sources))
                ui.markdown("""
                    Deploy to view the changes and output on all all your servers.  
                    """)

            sr.execution_report()
            er.execution_report()

        with ui.tab_panel('Ad-hoc Commands'):
            ui.label('Ad-hoc Commands')
            ah = Ad_Hoc()

            with ui.row():
                ui.switch('sudo', value=False).bind_value(ah, 'sudo')
                ui.switch('su', value=False).bind_value(ah, 'su')
                ui.input(label='Adhoc command').bind_value(ah, 'command')
                ui.markdown("""
                Type and Ad-hoc command, such as `hostname`.
                """)
            with ui.row():
                ui.button('Run', on_click=lambda: Perform_adhoc_command(inv, sr, er, ah))
                ui.markdown("""
                Run the command on all your servers.  
                """)
            sr.execution_report()
            er.execution_report()

        with ui.tab_panel('File Manager'):
            ui.label('File Manger')

            fp = File_path()
            with ui.row():
                ui.input(label='Server file path').bind_value(fp, 'path')
                ui.markdown("""
                This is the name of the file on the servers.  
                """)
            with ui.row():
                ui.button('Download File', on_click=lambda: Download_file(inv, fp, sr, er))
                ui.markdown("""
                Download the file content from the first server in the inventory.  
                """)
            with ui.row():
                ui.button('Upload File', on_click=lambda: pick_file(inv, fp, sr, er), icon='folder')
                ui.markdown("""
                Upload a file from the host to all the servers in the inventory.  
                """)

            sr.execution_report()
            er.execution_report()


        with ui.tab_panel('Package Manager'):
            ui.label('Package Manger')

            versions = Versions()
            manager = Manager()
            with ui.row():
                ui.select(['apk','pip','apt','dpkg','dnf', 'yum'],value='apk').bind_value(manager, 'manager')
                ui.markdown("""
                Choose a package manager from the dropdown list.  
                """)
            with ui.row():
                ui.button('Show installed packages', on_click=lambda: get_versions(inv, versions, manager, sr, er))
                ui.markdown("""
                Show all of the packages installed on each server.  
                """)
            versions.version_report()
            with ui.row():
                ui.switch('sudo',value=False).bind_value(manager, 'sudo')
                ui.switch('su',value=False).bind_value(manager, 'su')
                ui.input(label='Package').bind_value(manager, 'package')
                ui.button('Install package', on_click=lambda: install(inv, versions, manager, sr, er))
                ui.button('Remove package', on_click=lambda: remove(inv, versions, manager, sr, er))
                ui.markdown("""
                Install or remove a package from all servers in the inventory.  
                """)
            sr.execution_report()
            er.execution_report()

def _main():
    ui.run(title="Deployment Manager", reload=False, port=native.find_open_port(),
           storage_secret='private key to secure the browser session cookie')
