from nicegui import ui

from reemote.gui.download_file import download_file
from reemote.gui.file_path import File_path
from reemote.gui.pick_file import pick_file


def file_manager(tabs, inv, versions, manager, sr, er):
    with ui.tab_panels(tabs, value='File Manager').classes('w-full'):
        with ui.tab_panel('File Manager'):

            fp = File_path()
            with ui.row():
                ui.input(label='Server file path').bind_value(fp, 'path')
                ui.markdown("""
                This is the name of the file on the servers.  
                """)
            with ui.row():
                ui.button('Download File', on_click=lambda: download_file(inv, fp, sr, er))
                ui.markdown("""
                Download the file content from the first server in the inventory.  
                """)
            with ui.row():
                ui.button('Upload File', on_click=lambda: pick_file(inv, fp, sr, er), icon='folder')
                ui.markdown("""
                Upload a file from the host to all the servers in the inventory.  
                """)

            # sr.execution_report()
            # er.execution_report()
