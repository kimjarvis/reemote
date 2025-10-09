from nicegui import native
from reemote.gui.ad_hoc_manager import ad_hoc_manager
from reemote.gui.deployment_manager import deployment_manager
from reemote.gui.file_manager import file_manager
from reemote.gui.inventory_manager import inventory_manager
from reemote.gui.package_manager import package_manager

from nicegui import ui

@ui.page('/')
def page():
    tabs = ui_header()
    inventory_manager(tabs)
    file_manager(tabs)
    package_manager(tabs)
    ad_hoc_manager(tabs)
    deployment_manager(tabs)

def ui_header():
    with ui.header().classes(replace='row items-center') as header:
        with ui.tabs() as tabs:
            ui.tab('Inventory Manger')
            ui.tab('Deployment Manager')
            ui.tab('Command Manager')
            ui.tab('File Manager')
            ui.tab('Package Manager')
    return tabs

def _main():
    ui.run(title="Reemotecontrol", reload=False, port=native.find_open_port(),
           storage_secret='private key to secure the browser session cookie')
