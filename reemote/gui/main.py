from nicegui import native

from reemote.gui.inventory_upload import Inventory_upload
from reemote.gui.versions import Versions
from reemote.gui.manger import Manager
from reemote.gui.stdout_report import Stdout_report
from reemote.gui.execution_report import Execution_report

from reemote.gui.ad_hoc_manager import ad_hoc_manager
from reemote.gui.deployment_manager import deployment_manager
from reemote.gui.file_manager import file_manager
from reemote.gui.inventory_manager import inventory_manager
from reemote.gui.package_manager import package_manager
from nicegui import ui


@ui.page('/')
def page():
    inv=Inventory_upload()
    versions=Versions()
    manager=Manager()
    sr=Stdout_report()
    er=Execution_report()

    # Create the tabs and tab panels
    tabs, tab_panels = ui_header()

    # Add content for each tab
    with tab_panels:
        with ui.tab_panel('Inventory Manager'):
            inventory_manager(tabs, inv, versions, manager, sr, er)
        # with ui.tab_panel('Deployment Manager'):
        #     deployment_manager(tabs, inv, versions, manager, sr, er)
        # with ui.tab_panel('Command Manager'):
        #     ad_hoc_manager(tabs, inv, versions, manager, sr, er)
        # with ui.tab_panel('File Manager'):
        #     file_manager(tabs, inv, versions, manager, sr, er)
        # with ui.tab_panel('Package Manager'):
        #     package_manager(tabs, inv, versions, manager, sr, er)


def ui_header():
    # Create the header with tabs
    with ui.header().classes(replace='row items-center') as header:
        # Tabs component with 'Inventory Manager' as the default value
        with ui.tabs(value='Inventory Manager').props('dense') as tabs:
            ui.tab('Inventory Manager')
            # ui.tab('Deployment Manager')
            # ui.tab('Command Manager')
            # ui.tab('File Manager')
            # ui.tab('Package Manager')

        # Tab panels component bound to the tabs with 'Inventory Manager' as default
        tab_panels = ui.tab_panels(tabs, value='Inventory Manager').classes('w-full')

    return tabs, tab_panels


def _main():
    ui.run(
        title="Reemotecontrol",
        reload=False,
        port=native.find_open_port(),
        storage_secret='private key to secure the browser session cookie'
    )


if __name__ == '__main__':
    _main()