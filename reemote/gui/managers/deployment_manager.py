# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from nicegui import ui

from reemote.gui.functions.run_the_deploy import run_the_deploy


def deployment_manager(tabs, inventory, versions, manager, stdout_report, execution_report, source):
    with ui.tab_panels(tabs, value='Deployment Manager').classes('w-full'):
        with ui.tab_panel('Deployment Manager'):
            with ui.row():
                ui.button('Upload Source', on_click=lambda: source.pick_file(), icon='folder')
                ui.markdown("""
                Use the Upload button to upload a deployment builtin.

                A deployment builtin is python builtin that contains a list of Python classes, like this:

                ```python
                class Pacman_install_vim:
                    def execute(self):
                        from reemote.operations.pacman.packages import Packages
                        r = yield Packages(packages=["vim"],present=True, sudo=True)

                class Pacman_update:
                    def execute(self):
                        from reemote.operations.pacman.update import Update
                        # Update the packages on all hosts
                        r = yield Update(sudo=True)
                ```
                This builtin contains two deployments.

                - The first, Pacman_install_vim, installs vim.
                - The second, Pacman_update, updates all Packman packages.

                The deployment builtin format is described in detail [here](http://reemote.org/deployment.html).
                """)

            with ui.row():
                source.classes()
                ui.markdown("""
                Choose a deployment from the drop down list.  
                """)

            with ui.row():
                source._kwargs()
                ui.markdown("""
                Input the deployment arguments eg. "present=True".  
                """)

            with ui.row():
                ui.button('Deploy', on_click=lambda: run_the_deploy(inventory, execution_report, stdout_report, source))
                ui.markdown("""
                    Deploy to view the changes and output on all all your servers.  
                    """)

