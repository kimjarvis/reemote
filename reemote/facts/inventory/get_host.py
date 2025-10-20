# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command

class Get_host:
    """A reemote command object for retrieving the hostname.

    When used within the reemote framework, this class creates and executes
    a command to fetch the hostname of the target system. It is designed
    to be instantiated and its `execute` method yielded from a reemote task.

    Example:
        host = yield Get_host().execute()
        print(f"The hostname is: {host}")

    """
    def __repr__(self):
        return f"Get_host()"

    @staticmethod
    async def _get_host_callback(host_info, global_info, command, cp, caller):
        return host_info["host"]

    def execute(self):
        r = yield Command(
            f"{self}",
            local=True,
            callback=self._get_host_callback,
            caller=self
        )
