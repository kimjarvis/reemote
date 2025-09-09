from nicegui import ui, native, app, events
from nicegui.events import ValueChangeEventArguments
from reemote.run import run
from reemote.operations.filesystem.directory import Directory
from reemote.validate_inventory_structure import validate_inventory_structure
from reemote.verify_inventory_connect import verify_inventory_connect
from reemote.summarize_data_for_aggrid import summarize_data_for_aggrid
from reemote.construct_host_ops import construct_host_ops
from reemote.operations.filesystem.directory import Directory
from reemote.grid import grid

from reemote.operations.filesystem.directory import Directory

class Make_directory:
    def execute(self):
        yield Directory(path="/tmp/mydir", present=True, su=True)

class Gui:
    def __init__(self):
        app.storage.user["columns"] = [{'headerName': 'Command', 'field': 'command'}]
        app.storage.user["rows"] = []

    async def dir(self, present):
        operations, responses = await run(app.storage.user["inventory"],
                                          Make_directory())
        if present:
            ui.notify("Directory present")
        else:
            ui.notify("Directory absent")

        app.storage.user["columns"],app.storage.user["rows"] = grid(operations, responses)
        self.grid.refresh()

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
        app.storage.user["inventory"] = inventory()

        # Start with the fixed column definition for "Command"
        column_defs = [{'headerName': 'Command', 'field': 'command'}]

        # Dynamically generate column definitions for each host
        for index, (host_info, _) in enumerate(inventory()):
            host_ip = host_info['host']
            column_defs.append({'headerName': host_ip, 'field': f'host{index}'})
        app.storage.user["columns"] = column_defs
        self.grid.refresh()

    @ui.refreshable
    def grid(self):
        return ui.aggrid({
            'columnDefs': app.storage.user["columns"],
            'rowData': app.storage.user["rows"],
        }).classes('max-h-40  overflow-y-auto')

    def upload(self):
        return ui.upload(label="UPLOAD INVENTORY",
             on_upload=self.handle_upload,  # Handle the file upload
        ).props('accept=.py').classes('max-w-full')

    async def show(self, event: ValueChangeEventArguments):
        name = type(event.sender).__name__
        await self.dir(event.value)
        self.grid.refresh()


@ui.page('/')
def page():
    gui = Gui()

    ui.switch('Directory /tmp/mydir', value=False,
              on_change=gui.show,
              )

    gui.upload()
    gui.grid()


ui.run(title="Reemote", reload=False, port=native.find_open_port(),
       storage_secret='private key to secure the browser session cookie')
