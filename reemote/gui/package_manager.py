from nicegui import ui

from reemote.gui.get_versions import get_versions
from reemote.gui.install import install
from reemote.gui.manger import Manager
from reemote.gui.remove import remove
from reemote.gui.update import update
from reemote.gui.upgrade import upgrade
from reemote.gui.versions import Versions


def package_manager(tabs):
    with ui.tab_panels(tabs, value='Package Manager').classes('w-full'):
        with ui.tab_panel('Package Manager'):
            versions = Versions()
            manager = Manager()
            with ui.row():
                ui.select(['apk', 'pip', 'apt', 'dpkg', 'dnf', 'yum'], value='apk').bind_value(manager, 'manager')
                ui.markdown("""
                Choose a package manager from the dropdown list.  
                """)
            with ui.row():
                ui.button('Show installed packages', on_click=lambda: get_versions(inv, versions, manager, sr, er))
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
                ui.button('Install package', on_click=lambda: install(inv, versions, manager, sr, er))
                ui.button('Remove package', on_click=lambda: remove(inv, versions, manager, sr, er))
            with ui.row():
                ui.markdown("""
                Update or Upgrade packages on all servers in the inventory.  
                """)
                ui.button('Update', on_click=lambda: update(inv, versions, manager, sr, er))
                ui.button('Upgrade', on_click=lambda: upgrade(inv, versions, manager, sr, er))

            # sr.execution_report()
            # er.execution_report()
