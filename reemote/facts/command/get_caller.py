# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command

class Get_caller:
    """A reemote operation that returns the instance that initiated the command.

    This class is designed to be used within the reemote framework to allow
    a script to get a reference to the specific object that is currently
    executing. It is useful for introspection or for passing the object
    instance to other functions.

    The operation works by yielding a local `reemote.command.Command` that is
    constructed with the instance itself as the `caller` argument. An
    internal callback then returns this `caller` as the result of the
    command's execution.
    """
    def __repr__(self):
        return f"Get_caller()"

    async def _get_caller_callback(self, host_info, global_info, command, cp, caller):
        return caller

    def execute(self):
        r = yield Command(
            f"{self}",
            local=True,
            callback=self._get_caller_callback,
            caller=self
        )