import sys
from nicegui import ui, native, app, events
from reemote.gui import Gui
from reemote.execute import execute
from reemote.produce_grid import produce_grid
from reemote.produce_json import produce_json
from reemote.operations.server.shell import Shell
from reemote.verify_source_file_contains_valid_class import verify_source_file_contains_valid_class
from reemote.validate_root_class_name_and_get_root_class import validate_root_class_name_and_get_root_class
from reemote.deploy_manager.local_file_picker import local_file_picker
from reemote.get_classes_in_source import get_classes_in_source

class Gui1:
    def __init__(self):
        app.storage.user["stdout"] = ""

    @ui.refreshable
    def stdout(self):
        # return ui.code(app.storage.user["stdout"],language="bash").classes('w-full')
        ui.label(app.storage.user["stdout"])

class Gui2:
    def __init__(self):
        app.storage.user["classes"] = ["None"]
        app.storage.user["source"] = "~"

    @ui.refreshable
    def classes(self):
        return ui.select(app.storage.user["classes"]).bind_value(app.storage.user, 'deployment')

    async def pick_file(self) -> None:
        result = await local_file_picker('~', multiple=False)
        ui.notify(f'Uploading file {result}')
        app.storage.user["source"] = result
        app.storage.user["classes"] = get_classes_in_source(result[0])
        self.classes.refresh()

class Wrapper:

    def __init__(self, command):
        self.command = command

    def execute(self):
        # Execute a shell command on all hosts
        r = yield self.command()
        # The result is available in stdout
        print(r.cp.stdout)


async def run_the_deploy(gui, gui1):
    # Verify class and method
    app.storage.user["source"]=app.storage.user["source"][0]
    print(app.storage.user["source"])
    if app.storage.user["source"] != "/":
        print(app.storage.user["deployment"])

        if app.storage.user["source"] and app.storage.user["deployment"]:
            if not verify_source_file_contains_valid_class(app.storage.user["source"], app.storage.user["deployment"]):
                sys.exit(1)

        # Verify the source and class
        if app.storage.user["source"] and app.storage.user["deployment"]:
            root_class = validate_root_class_name_and_get_root_class(app.storage.user["deployment"], app.storage.user["source"])
            if not root_class:
                sys.exit(1)

        responses = await execute(app.storage.user["inventory"], Wrapper(root_class))
        print(responses)
        # responses[0] is the output of the info command.
        app.storage.user["stdout"] = responses[0].cp.stdout
        print(app.storage.user["stdout"])
        gui1.stdout.refresh()
        app.storage.user["columnDefs"],app.storage.user["rowData"] = produce_grid(produce_json(responses))
        gui.execution_report.refresh()

@ui.page('/')
def page():
    gui = Gui()
    gui1 = Gui1()
    gui2 = Gui2()
    gui.upload_inventory()
    ui.button('Upload Source', on_click=lambda: gui2.pick_file(), icon='folder')
    ui.input(label='Deployment').bind_value(app.storage.user, 'deployment')
    ui.button('Deploy', on_click=lambda: run_the_deploy(gui, gui1))
    gui2.classes()
    gui1.stdout()
    gui.execution_report()

ui.run(title="Ad Hoc Controller", reload=False, port=native.find_open_port(),
       storage_secret='private key to secure the browser session cookie')
