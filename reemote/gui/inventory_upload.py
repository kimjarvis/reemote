from nicegui import events, ui

from reemote.utilities.validate_inventory_structure import validate_inventory_structure
from reemote.utilities.verify_inventory_connect import verify_inventory_connect


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
