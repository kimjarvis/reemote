# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from nicegui import ui
from reemote.gui.functions.inv_upload import upload_inventory


def inventory_manager(tabs, inventory, versions, manager, stdout_report, execution_report):
    with ui.tab_panels(tabs, value='Inventory Manager').classes('w-full'):
        with ui.tab_panel('Inventory Manager'):

            async def combined_upload_handler(e):
                await inventory.handle_upload(e)  # Handle the upload first
                await upload_inventory(inventory, execution_report, stdout_report)  # Then run your setup logic

            with ui.row():
                ui.upload(
                    label="UPLOAD INVENTORY",
                    on_upload=combined_upload_handler
                ).props('accept=.py').classes('max-w-full')

                ui.markdown("""
                Use the + to upload an inventory builtin.
    
                An inventory is a python builtin that defines an inventory() function, like this:
    
                ```python
                from typing import List, Tuple, Dict, Any
    
                def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
                   return [
                      (
                          {
                              'host': '10.156.135.16',  # alpine
                              'username': 'user',  # User name
                              'password': 'user'  # Password
                          },
                          {
                              'su_user': 'root',
                              'su_password': 'root'  # Password
                          }
                      )
                  ]
                ```
                It is a list of tuples, each containing two dictionaries.
    
                - The first, contains the parameters of Asyncio connect.
                - The second, contains information for su and sudo access and global values.
    
                The inventory builtin format is described in detail [here](http://reemote.org/inventory.html).
                """)

