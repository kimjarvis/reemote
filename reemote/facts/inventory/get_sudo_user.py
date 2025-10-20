# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command

class Get_sudo_user:
    """Represents a Reemote operation to retrieve the configured sudo user.

    This class encapsulates the logic for fetching the 'sudo_user' value
    from the global information context within the Reemote framework. It is
    designed to be used as part of a Reemote execution plan. When its
    `execute` method is called, it yields a local command that resolves
    to the sudo username.
    """
    def __repr__(self):
        return f"Get_sudo_user()"

    @staticmethod
    async def _get_sudo_user_callback(host_info, global_info, command, cp, caller):
        return global_info["sudo_user"]

    def execute(self):
        r = yield Command(
            f"{self}",
            local=True,
            callback=self._get_sudo_user_callback,
            caller=self
        )
