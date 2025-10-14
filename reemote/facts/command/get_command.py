# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command

class Get_command:
    """A command-like object used to retrieve the underlying `Command` instance.

    This class provides a mechanism for introspecting the command execution
    framework. When its `execute` method is used, it yields a special `Command`
    object configured to run locally.

    The framework processes this yielded command, and the associated callback,
    `_get_command_callback`, simply returns the `command` object it receives
    as an argument. This allows the caller of `execute` to obtain a direct
    reference to the `Command` instance that was created and processed.
    """
    def __repr__(self):
        return f"Get_command()"

    async def _get_command_callback(self, host_info, global_info, command, cp, caller):
        return command

    def execute(self):
        r = yield Command(
            f"{self}",
            local=True,
            callback=self._get_command_callback,
            caller=self
        )