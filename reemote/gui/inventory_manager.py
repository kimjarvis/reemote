from nicegui import ui

from reemote.gui.execution_report import Execution_report
from reemote.gui.inv_upload import inv_upload
from reemote.gui.inventory_upload import Inventory_upload
from reemote.gui.source_upload import Sources_upload
from reemote.gui.stdout_report import Stdout_report


def inventory_manager(tabs, inv, versions, manager, sr, er):
    with ui.tab_panels(tabs, value='Inventory Manager').classes('w-full'):
        with ui.tab_panel('Inventory Manager'):

            async def combined_upload_handler(e):
                await inv.handle_upload(e)  # Handle the upload first
                await inv_upload(inv, er, sr)  # Then run your setup logic

            with ui.row():
                ui.upload(
                    label="UPLOAD INVENTORY",
                    on_upload=combined_upload_handler
                ).props('accept=.py').classes('max-w-full')
                ui.markdown("""
                Use the + to upload an inventory file.  
                
                An inventory is a python file that defines an inventory() function, like this:
                
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
                
                The inventory file format is described in detail [here](http://reemote.org/inventory.html).
                """)
            sr.execution_report()
            er.execution_report()
