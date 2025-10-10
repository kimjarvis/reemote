from nicegui import ui

from reemote.gui.functions.download_file import download_file
from reemote.gui.classes.file_path import File_path
from reemote.gui.functions.pick_file import pick_file


def file_manager(tabs, inventory, versions, manager, stdout_report, execution_report, file_path):
    with ui.tab_panels(tabs, value='File Manager').classes('w-full'):
        with ui.tab_panel('File Manager'):


            with ui.row():
                ui.input(label='Server file path').bind_value(file_path, 'path')
                ui.markdown("""
                This is the name of the file on the servers.  
                """)
            with ui.row():
                ui.button('Download File', on_click=lambda: download_file(inventory, file_path, stdout_report, execution_report))
                ui.markdown("""
                Download the file content from the first server in the inventory.  
                """)
            with ui.row():
                ui.button('Upload File', on_click=lambda: pick_file(inventory, file_path, stdout_report, execution_report), icon='folder')
                ui.markdown("""
                Upload a file from the host to all the servers in the inventory.  
                """)

            # stdout_report.execution_report()
            # execution_report.execution_report()
