# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command

class Get_username:
    """Represents a command to retrieve the username from host information.

    This class is designed to be used within the reemote framework. When its
    `execute` method is used in a coroutine, it yields a `Command` object
    configured to run locally and retrieve the 'username' from the `host_info`
    dictionary provided by the framework's execution context.

    This provides a standardized, reusable way to access the current host's
    username within a reemote task sequence.
    """
    def __repr__(self):
        return f"Get_username()"

    @staticmethod
    async def _get_username_callback(host_info, global_info, command, cp, caller):
        return host_info["username"]

    def execute(self):
        r = yield Command(
            f"{self}",
            local=True,
            callback=self._get_username_callback,
            caller=self
        )
