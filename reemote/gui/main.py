# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from nicegui import native

from reemote.gui.classes.inventory_upload import Inventory_upload
from reemote.gui.classes.versions import Versions
from reemote.gui.classes.manger import Manager
from reemote.gui.classes.stdout_report import Stdout_report
from reemote.gui.classes.execution_report import Execution_report
from reemote.gui.classes.source_upload import Sources_upload
from reemote.gui.classes.file_path import File_path
from reemote.gui.classes.ad_hoc import Ad_Hoc

from reemote.gui.managers.command_manager import command_manager
from reemote.gui.managers.deployment_manager import deployment_manager
from reemote.gui.managers.file_manager import file_manager
from reemote.gui.managers.inventory_manager import inventory_manager
from reemote.gui.managers.package_manager import package_manager
from nicegui import ui

@ui.page('/')
def index():
    inventory=Inventory_upload()
    versions=Versions()
    manager=Manager()
    stdout_report=Stdout_report()
    execution_report=Execution_report()
    source=Sources_upload()
    file_path = File_path()
    ad_hoc = Ad_Hoc()

    with ui.header().classes(replace='row items-center') as header:
        with ui.tabs().classes('w-full') as tabs:
            ui.tab('Inventory Manager')
            ui.tab('Deployment Manager')
            ui.tab('Command Manager')
            ui.tab('File Manager')
            ui.tab('Package Manager')

    with ui.tab_panels(tabs,value='Inventory Manager').classes('w-full') as panels:
        with ui.tab_panel('Inventory Manager'):
            ui.label('Inventory Manager')
            inventory_manager(tabs, inventory, versions, manager, stdout_report, execution_report)
        with ui.tab_panel('Deployment Manager'):
            ui.label('Deployment Manager')
            deployment_manager(tabs, inventory, versions, manager, stdout_report, execution_report, source)
        with ui.tab_panel('Command Manager'):
            ui.label('Command Manager')
            command_manager(tabs, inventory, versions, manager, stdout_report, execution_report, ad_hoc)
        with ui.tab_panel('File Manager'):
            ui.label('File Manager')
            file_manager(tabs, inventory, versions, manager, stdout_report, execution_report, file_path)
        with ui.tab_panel('Package Manager'):
            ui.label('Package Manager')
            package_manager(tabs, inventory, versions, manager, stdout_report, execution_report)

    with ui.footer() as footer:
        stdout_report.execution_report()
        execution_report.execution_report()

ui.run(
    title="Reemotecontrol",
    reload=False,
    port=native.find_open_port(),
    storage_secret='private key to secure the browser session cookie'
)


