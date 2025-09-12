from nicegui import ui, native, app
from reemote.gui import Gui
from reemote.run import run
from reemote.produce_grid import produce_grid
from reemote.produce_json import produce_json
from reemote.operations.filesystem.directory import Directory


async def Control_directory(gui):
    _, responses = await run(app.storage.user["inventory"],
                                      Directory(path="/tmp/mydir", present=app.storage.user["present"], su=True))
    app.storage.user["columnDefs"],app.storage.user["rowData"] = produce_grid(produce_json(responses))
    gui.execution_report.refresh()

@ui.page('/')
def page():
    gui = Gui()
    gui.upload_inventory()
    ui.switch('Directory /tmp/mydir is present on hosts', value=False).bind_value(app.storage.user, 'present')
    ui.button('Run', on_click=lambda: Control_directory(gui))
    gui.execution_report()


ui.run(title="Manage directory", reload=False, port=native.find_open_port(),
       storage_secret='private key to secure the browser session cookie')
