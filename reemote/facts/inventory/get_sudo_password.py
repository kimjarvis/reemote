# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command

class Get_sudo_password:
    """A reemote command to retrieve the sudo password from the global context.

    This class is designed to be used within a reemote execution plan. It
    provides a way to access the sudo password without hardcoding it
    or passing it directly as a parameter to other functions.

    When its `execute` method is called, it yields a local command. The
    reemote runner processes this command by invoking a callback function
    that retrieves the password from the `global_info` dictionary. The
    runner is responsible for populating `global_info['sudo_password']`
    before this command runs.
    """
    def __repr__(self):
        return f"Get_sudo_password()"

    @staticmethod
    async def _get_sudo_password_callback(host_info, global_info, command, cp, caller):
        return global_info["sudo_password"]

    def execute(self):
        r = yield Command(
            f"{self}",
            local=True,
            callback=self._get_sudo_password_callback,
            caller=self
        )
