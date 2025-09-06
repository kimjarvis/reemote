from nicegui import ui, native, app, events
from nicegui.events import ValueChangeEventArguments
from reemote.run import run
from reemote.operations.filesystem.directory import Directory
from reemote.validate_inventory_structure import validate_inventory_structure
from reemote.verify_inventory_connect import verify_inventory_connect
from reemote.printers import construct_host_ops, generate_change_df, convert_df_to_ag_grid
from reemote.printers import construct_host_ops, print_json_ops, print_json_ops, summarize_data_for_aggrid, get_printable_aggrid

class Make_directory:
    def __init__(self, present):
        self.present = present

    def execute(self):
        yield Directory(path="/tmp/mydir", present=self.present, sudo=False)

class Gui:
    def __init__(self):
        app.storage.user["columns"]=[]
        app.storage.user["rows"]=[]

    async def dir(self,present):
        operations, responses = await run(app.storage.user["inventory"], Make_directory(present=present))
        if present:
            ui.notify("Directory present")
        else:
            ui.notify("Directory absent")

        host_ops = construct_host_ops(operations, responses)
        dgrid = summarize_data_for_aggrid(host_ops)
        app.storage.user["columns"] = dgrid.get("columnDefs")
        app.storage.user["rows"] = dgrid.get("rowData")
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

    @ui.refreshable
    def grid(self):
        return ui.aggrid({
            'columnDefs': app.storage.user["columns"],
            'rowData': app.storage.user["rows"],
        }).classes('max-h-40  overflow-y-auto')


    async def show(self,event: ValueChangeEventArguments):
        name = type(event.sender).__name__
        await self.dir(event.value)
        self.grid.refresh()


@ui.page('/')
def page():
    gui = Gui()

    ui.switch('Directory /tmp/mydir', value=False,
              on_change=gui.show,
              )

    ui.upload(label="UPLOAD INVENTORY",
              on_upload=gui.handle_upload,  # Handle the file upload
              ).props('accept=.py').classes('max-w-full')

    gui.grid()

ui.run(title="Reemote RC Demo", reload=False, port=native.find_open_port(),
       storage_secret='private key to secure the browser session cookie')
