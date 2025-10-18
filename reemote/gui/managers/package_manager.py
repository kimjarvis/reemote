# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from nicegui import ui

from reemote.gui.functions.get_versions import get_versions
from reemote.gui.functions.install import install
from reemote.gui.functions.remove import remove
from reemote.gui.functions.update import update
from reemote.gui.functions.upgrade import upgrade


def package_manager(tabs, inventory, versions, manager, stdout_report, execution_report):
    with ui.tab_panels(tabs, value='Package Manager').classes('w-full'):
        with ui.tab_panel('Package Manager'):
            with ui.row():
                ui.select(['apk', 'pip', 'apt', 'dpkg', 'dnf', 'yum'], value='apk').bind_value(manager, 'manager')
                ui.markdown("""
                Choose a package manager from the dropdown list.  
                """)
            with ui.row():
                ui.button('Show installed packages', on_click=lambda: get_versions(inventory, versions, manager, stdout_report, execution_report))
                ui.markdown("""
                Show all of the packages installed on each server.  
                """)
            versions.version_report()
            with ui.row():
                ui.markdown("""
                Install or remove a package from all servers in the inventory.  
                """)
                ui.switch('sudo', value=False).bind_value(manager, 'sudo')
                ui.switch('su', value=False).bind_value(manager, 'su')
                ui.input(label='Package').bind_value(manager, 'package')
                ui.button('Install package', on_click=lambda: install(inventory, versions, manager, stdout_report, execution_report))
                ui.button('Remove package', on_click=lambda: remove(inventory, versions, manager, stdout_report, execution_report))
            with ui.row():
                ui.markdown("""
                Update or Upgrade packages on all servers in the inventory.  
                """)
                ui.button('Update', on_click=lambda: update(inventory, versions, manager, stdout_report, execution_report))
                ui.button('Upgrade', on_click=lambda: upgrade(inventory, versions, manager, stdout_report, execution_report))

