from nicegui import ui, native, app
from reemote.gui import Gui
from reemote.execute import execute
from reemote.utilities.produce_grid import produce_grid
from reemote.utilities.produce_json import produce_json
from reemote.operations.server.shell import Shell

class Hello_world:
    def __init__(self, output=None):
        self.output=output

    def execute(self):
        from reemote.operations.server.shell import Shell
        r = yield Shell("echo Hello World!")
        print(r.cp.stdout)

async def run_shell(gui):
    responses = await execute(app.storage.user["inventory"],Shell("echo Hello World!"))
    app.storage.user["columnDefs"],app.storage.user["rowData"] = produce_grid(produce_json(responses))
    gui.execution_report.refresh()

@ui.page('/')
def page():
    gui = Gui()
    gui.upload_inventory()
    hw=Hello_world()
    ui.label().bind_text_from(Hello_world, "ouput")
    ui.button('Run', on_click=lambda: run_shell(gui))
    gui.execution_report()


ui.run(title="Hello World", reload=False, port=native.find_open_port(),
       storage_secret='private key to secure the browser session cookie')
