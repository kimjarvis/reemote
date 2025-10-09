from nicegui import ui

from reemote.gui.ad_hoc import Ad_Hoc
from reemote.gui.perform_adhoc_command import perform_adhoc_command


def ad_hoc_manager(tabs):
    with ui.tab_panels(tabs, value='Command Manager').classes('w-full'):
        with ui.tab_panel('Command Manager'):
            ah = Ad_Hoc()

            with ui.row():
                ui.switch('sudo', value=False).bind_value(ah, 'sudo')
                ui.switch('su', value=False).bind_value(ah, 'su')
                ui.input(label='Adhoc command').bind_value(ah, 'command')
                ui.markdown("""
                Type and Ad-hoc command, such as `hostname`.
                """)
            with ui.row():
                ui.button('Run', on_click=lambda: perform_adhoc_command(inv, sr, er, ah))
                ui.markdown("""
                Run the command on all your servers.  
                """)
            # sr.execution_report()
            # er.execution_report()
