import sys
from nicegui import native
from reemote.execute import execute
from reemote.produce_grid import produce_grid
from reemote.produce_output_grid import produce_output_grid
from reemote.produce_json import produce_json
from reemote.verify_source_file_contains_valid_class import verify_source_file_contains_valid_class
from reemote.validate_root_class_name_and_get_root_class import validate_root_class_name_and_get_root_class
from development.deploy_manager.local_file_picker import local_file_picker
from reemote.get_classes_in_source import get_classes_in_source
from reemote.operations.server.shell import Shell
from reemote.operations.filesystem.get_file import Get_file
from reemote.operations.filesystem.put_file import Put_file


from nicegui import events, ui

from reemote.validate_inventory_structure import validate_inventory_structure
from reemote.verify_inventory_connect import verify_inventory_connect

class Execution_report:
    def __init__(self):
        self.columns = [{'headerName': 'Command', 'field': 'command'}]
        self.rows = []

    def set(self, columns, rows):
        self.columns = columns
        self.rows = rows

    @ui.refreshable
    def execution_report(self):
        return ui.aggrid({
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
        return ui.aggrid({
            'columnDefs': self.columns,
            'rowData': self.rows,
        }).classes('max-h-40  overflow-y-auto')

class Sources_upload:
    def __init__(self):
        self.source= "/"
        self._classes = []
        self.deployment = ""

    @ui.refreshable
    def classes(self):
        return ui.select(self._classes).bind_value(self, 'deployment')

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
    print(columns)
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

        responses = await execute(inv.inventory, Wrapper(root_class))
        c, r =produce_grid(produce_json(responses))
        er.set(c, r)
        # print("trace er")
        # print(c)
        # print(r)
        er.execution_report.refresh()
        c, r =produce_output_grid(produce_json(responses))
        stdout.set(c, r)
        # print("trace stdout")
        # print(c)
        # print(r)
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
    # print("trace er")
    # print(c)
    # print(r)
    er.execution_report.refresh()
    c, r = produce_output_grid(produce_json(responses))
    sr.set(c, r)
    # print("trace stdout")
    # print(c)
    # print(r)
    sr.execution_report.refresh()

    # app.storage.user["stdout"] = responses[0].cp.stdout
    # gui1.stdout.refresh()
    # app.storage.user["columnDefs"],app.storage.user["rowData"] = produce_grid(produce_json(responses))
    # gui.execution_report.refresh()


class File_path:
    def __init__(self):
        self.path = ""

async def Download_file(inv, fp, sr, er):
    responses = await execute(inv.inventory,Get_file(path=fp.path,host=inv.inventory)) # [0][0]['host']
    c, r = produce_grid(produce_json(responses))
    er.set(c, r)
    # print("trace er")
    # print(c)
    # print(r)
    er.execution_report.refresh()
    c, r = produce_output_grid(produce_json(responses))
    sr.set(c, r)
    # print("trace stdout")
    # print(c)
    # print(r)
    sr.execution_report.refresh()

async def pick_file(inv, fp, sr, er) -> None:
    result = await local_file_picker('~', multiple=False)
    ui.notify(f'Uploading file {result}')
    with open(result[0], 'r', encoding='utf-8') as file:
        text = file.read()
    responses = await execute(inv.inventory,Put_file(path=fp.path,text=text))
    c, r = produce_grid(produce_json(responses))
    er.set(c, r)
    # print("trace er")
    # print(c)
    # print(r)
    er.execution_report.refresh()
    c, r = produce_output_grid(produce_json(responses))
    sr.set(c, r)
    # print("trace stdout")
    # print(c)
    # print(r)
    sr.execution_report.refresh()

@ui.page('/')
def page():
    with ui.header().classes(replace='row items-center') as header:
        with ui.tabs() as tabs:
            ui.tab('Inventory')
            ui.tab('Deployment Manager')
            ui.tab('Ad Hoc Commands')
            ui.tab('File Manager')
            ui.tab('File Package')

    with ui.tab_panels(tabs, value='Inventory').classes('w-full'):
        with ui.tab_panel('Inventory'):
            sr = Stdout_report()
            er = Execution_report()
            sources = Sources_upload()
            inv = Inventory_upload()

            async def combined_upload_handler(e):
                await inv.handle_upload(e)  # Handle the upload first
                await inv_upload(inv, er, sr, sources)  # Then run your setup logic

            ui.upload(
                label="UPLOAD INVENTORY",
                on_upload=combined_upload_handler
            ).props('accept=.py').classes('max-w-full')
            sr.execution_report()
            er.execution_report()

        with ui.tab_panel('Deployment Manager'):
            ui.button('Upload Source', on_click=lambda: sources.pick_file(), icon='folder')
            sources.classes()
            ui.button('Deploy', on_click=lambda: run_the_deploy(inv, er, sr, sources))
            sr.execution_report()
            er.execution_report()

        with ui.tab_panel('Ad Hoc Commands'):
            ui.label('Ad Hoc Commands')
            ah = Ad_Hoc()

            with ui.row():
                ui.switch('sudo', value=False).bind_value(ah, 'sudo')
                ui.switch('su', value=False).bind_value(ah, 'su')
                ui.input(label='Adhoc command').bind_value(ah, 'command')
            ui.button('Run', on_click=lambda: Perform_adhoc_command(inv, sr, er, ah))
            sr.execution_report()
            er.execution_report()

        with ui.tab_panel('File Manager'):
            ui.label('File Manger')

            fp = File_path()
            ui.input(label='Server file path').bind_value(fp, 'path')
            ui.button('Download File', on_click=lambda: Download_file(inv, fp, sr, er))
            ui.button('Upload File', on_click=lambda: pick_file(inv, fp, sr, er), icon='folder')

            sr.execution_report()
            er.execution_report()


        with ui.tab_panel('Package Manager'):
            ui.label('Package Manger')

ui.run(title="Deployment Manager", reload=False, port=native.find_open_port(),
       storage_secret='private key to secure the browser session cookie')
