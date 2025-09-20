import sys
from nicegui import ui, native, app, events
from reemote.execute import execute
from reemote.produce_grid import produce_grid
from reemote.produce_output_grid import produce_output_grid
from reemote.produce_json import produce_json
from reemote.verify_source_file_contains_valid_class import verify_source_file_contains_valid_class
from reemote.validate_root_class_name_and_get_root_class import validate_root_class_name_and_get_root_class
from reemote.deploy_manager.local_file_picker import local_file_picker
from reemote.get_classes_in_source import get_classes_in_source

from nicegui import app, events, ui

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


@ui.page('/')
def page():
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

    ui.button('Upload Source', on_click=lambda: sources.pick_file(), icon='folder')
    sources.classes()
    ui.button('Deploy', on_click=lambda: run_the_deploy(inv, er, sr, sources))
    sr.execution_report()
    er.execution_report()

ui.run(title="Deployment Manager", reload=False, port=native.find_open_port(),
       storage_secret='private key to secure the browser session cookie')
