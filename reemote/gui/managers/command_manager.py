# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from nicegui import ui

from reemote.gui.classes.ad_hoc import Ad_Hoc
from reemote.gui.functions.perform_adhoc_command import perform_adhoc_command


def command_manager(tabs, inventory, versions, manager, stdout_report, execution_report, ad_hoc):
    with ui.tab_panels(tabs, value='Command Manager').classes('w-full'):
        with ui.tab_panel('Command Manager'):

            with ui.row():
                ui.switch('sudo', value=False).bind_value(ad_hoc, 'sudo')
                ui.switch('su', value=False).bind_value(ad_hoc, 'su')
                ui.input(label='Adhoc command').bind_value(ad_hoc, 'command')
                ui.markdown("""
                Type and Ad-hoc command, such as `hostname`.
                """)
            with ui.row():
                ui.button('Run', on_click=lambda: perform_adhoc_command(inventory, stdout_report, execution_report, ad_hoc))
                ui.markdown("""
                Run the command on all your servers.  
                """)

