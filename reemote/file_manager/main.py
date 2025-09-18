from nicegui import ui, native, app
from reemote.gui import Gui
from reemote.execute import execute
from reemote.produce_grid import produce_grid
from reemote.produce_json import produce_json
from reemote.operations.filesystem.get_file import Get_file
from reemote.operations.filesystem.put_file import Put_file
from reemote.file_manager.local_file_picker import local_file_picker

class Gui1:
    def __init__(self):
        app.storage.user["stdout"] = ""

    @ui.refreshable
    def stdout(self):
        return ui.code(app.storage.user["stdout"],language="bash").classes('w-full')

async def Download_file(gui,gui1):
    responses = await execute(app.storage.user["inventory"],Get_file(path=app.storage.user["path"],host=app.storage.user["inventory"][0][0]['host']))
    app.storage.user["columnDefs"],app.storage.user["rowData"] = produce_grid(produce_json(responses))
    app.storage.user["stdout"] = responses[0].cp.stdout
    gui1.stdout.refresh()
    app.storage.user["columnDefs"],app.storage.user["rowData"] = produce_grid(produce_json(responses))
    gui.execution_report.refresh()

async def pick_file(gui) -> None:
    result = await local_file_picker('~', multiple=False)
    ui.notify(f'Uploading file {result}')
    with open(result[0], 'r', encoding='utf-8') as file:
        app.storage.user["text"] = file.read()
    responses = await execute(app.storage.user["inventory"],Put_file(path=app.storage.user["path"],text=app.storage.user["text"]))
    app.storage.user["columnDefs"],app.storage.user["rowData"] = produce_grid(produce_json(responses))
    gui.execution_report.refresh()

@ui.page('/')
def page():
    gui = Gui()
    gui1 = Gui1()
    gui.upload_inventory()
    ui.input(label='Server file path').bind_value(app.storage.user, 'path')
    ui.button('Download File', on_click=lambda: Download_file(gui,gui1))
    ui.label("File contents:")
    gui1.stdout()
    ui.button('Upload File', on_click=lambda: pick_file(gui), icon='folder')
    gui.execution_report()

def _main_fm():
    ui.run(title="File Manager", reload=False, port=native.find_open_port(),
           storage_secret='private key to secure the browser session cookie')
