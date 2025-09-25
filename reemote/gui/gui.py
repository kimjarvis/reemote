from nicegui import app, events, ui

from reemote.utilities.validate_inventory_structure import validate_inventory_structure
from reemote.utilities.verify_inventory_connect import verify_inventory_connect


class Gui:
    def __init__(self):
        app.storage.user["columnDefs"] = [{'headerName': 'Command', 'field': 'command'}]
        app.storage.user["rowData"] = []


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
        columnDefs = [{'headerName': 'Command', 'field': 'command'}]

        # Dynamically generate column definitions for each host
        for index, (host_info, _) in enumerate(inventory()):
            host_ip = host_info['host']
            columnDefs.append({'headerName': f'{host_ip} Executed', 'field': f'{host_ip.replace(".","_")}_executed'})
            columnDefs.append({'headerName': f'{host_ip} Changed', 'field': f'{host_ip.replace(".","_")}_changed'})
        app.storage.user["columnDefs"] = columnDefs
        self.execution_report.refresh()

    @ui.refreshable
    def execution_report(self):
        return ui.aggrid({
            'columnDefs': app.storage.user["columnDefs"],
            'rowData': app.storage.user["rowData"],
        }).classes('max-h-40  overflow-y-auto')

    def upload_inventory(self):
        return ui.upload(label="UPLOAD INVENTORY",
             on_upload=self.handle_upload,  # Handle the file upload
        ).props('accept=.py').classes('max-w-full')
