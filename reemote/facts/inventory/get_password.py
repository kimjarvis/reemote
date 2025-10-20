# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command

class Get_password:
    """A reemote command to retrieve a host's password from the execution context.

    This class is designed to be used within a reemote execution plan. It
    encapsulates the logic for securely accessing the password associated with a
    host, which is stored in the `host_info` dictionary provided by the reemote
    runner.

    When `execute` is called, it yields a local `Command` that uses an
    internal callback to perform the password retrieval.
    """
    def __repr__(self):
        return f"Get_password()"

    @staticmethod
    async def _get_password_callback(host_info, global_info, command, cp, caller):
        return host_info["password"]

    def execute(self):
        r = yield Command(
            f"{self}",
            local=True,
            callback=self._get_password_callback,
            caller=self
        )
