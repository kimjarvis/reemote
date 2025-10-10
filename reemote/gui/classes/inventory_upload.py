from nicegui import events, ui

from reemote.utilities.validate_inventory_structure import validate_inventory_structure
from reemote.utilities.verify_inventory_connect import verify_inventory_connect


class Inventory_upload:
    def __init__(self):
        self.inventory = None

    async def handle_upload(self, e: events.UploadEventArguments):
        print("Upload event attributes:", dir(e))
        print("Upload event:", e)

        # Access the file data directly from the file._data attribute
        try:
            if hasattr(e, 'file') and hasattr(e.file, '_data'):
                text = e.file._data.decode('utf-8')
                print("Successfully read file content")
            else:
                ui.notify("Error: Uploaded file data not found")
                return
        except Exception as ex:
            ui.notify(f"Error reading file: {str(ex)}")
            return

        # Execute the uploaded Python code
        try:
            # Create a clean namespace for execution
            exec_globals = {}
            exec(text, exec_globals)

            # Check if inventory function exists
            if 'inventory' not in exec_globals or not callable(exec_globals['inventory']):
                ui.notify("Error: No 'inventory' function found in the uploaded file")
                return

            inventory_func = exec_globals['inventory']

        except Exception as ex:
            ui.notify(f"Error executing inventory code: {str(ex)}")
            return

        # Validate the inventory
        try:
            inventory_obj = inventory_func()

            if not validate_inventory_structure(inventory_obj):
                ui.notify("Inventory structure is invalid")
                return

            if not await verify_inventory_connect(inventory_obj):
                ui.notify("Inventory connections are invalid")
                return

            ui.notify("Inventory structure and all hosts connect")
            self.inventory = inventory_obj
            print(f"Inventory set successfully: {self.inventory}")

        except Exception as ex:
            ui.notify(f"Error validating inventory: {str(ex)}")