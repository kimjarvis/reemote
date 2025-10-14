# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from nicegui import ui

from reemote.gui.functions.download_file import download_file
from reemote.gui.local_file_picker.local_file_picker import local_file_picker
from reemote.gui.functions.upload_file import upload_file

class File_picker:
    def __init__(self):
        self.paths= "/"

async def pick_file(fp: File_picker) -> None:
    result = await local_file_picker('~', multiple=True)
    ui.notify(f'You chose {result}')
    fp.paths=result

def file_manager(tabs, inventory, versions, manager, stdout_report, execution_report, file_path):

    fp = File_picker()

    with ui.tab_panels(tabs, value='File Manager').classes('w-full'):
        with ui.tab_panel('File Manager'):


            with ui.row():
                ui.input(label='Server builtin path').bind_value(file_path, 'path')
                ui.markdown("""
                This is the name of the builtin on the servers.  
                """)
            with ui.row():
                ui.button('Download File', on_click=lambda: download_file(inventory, file_path, stdout_report, execution_report))
                ui.markdown("""
                Download the builtin content from the first server in the inventory.  
                """)
            with ui.row():
                ui.button('Choose Files', on_click=lambda: pick_file(fp), icon='folder')
                ui.markdown("""
                Choose files on the local host.  
                """)
            with ui.row():
                ui.button('Upload Files', on_click=lambda: upload_file(fp, inventory, file_path, stdout_report, execution_report))
                ui.markdown("""
                Upload a builtin from the local host to all the servers in the inventory.  
                """)

