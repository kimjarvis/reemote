# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command

class Get_cp:
    """A command-like object to retrieve the current command processor.

    This class is designed to be used within the reemote command execution
    framework. When its `execute` method is called, it yields a special
    local command. The framework intercepts this command and, via a
    callback, provides the instance of the currently running command
    processor (`cp`).

    This is useful for other commands that need to introspect or interact
    with the command processor that is executing them.
    """
    def __repr__(self):
        return f"Get_cp()"

    async def _get_cp_callback(self, host_info, global_info, command, cp, caller):
        return cp

    def execute(self):
        r = yield Command(
            f"{self}",
            local=True,
            callback=self._get_cp_callback,
            caller=self
        )