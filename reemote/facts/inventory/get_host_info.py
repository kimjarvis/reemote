# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command

class Get_host_info:
    """Represents a command to retrieve specific information about the host.

    This class is designed to be used within the reemote framework. It
    constructs a local command that accesses the `host_info` dictionary
    provided by the reemote runner. An instance of this class is a
    generator-based task that, when executed, yields a `Command` object.

    Attributes:
        field (str | None): The key of the information to retrieve from the
            host's info dictionary.
    """
    def __init__(self, field=None):
        if field is not None and not isinstance(field, str):
            raise ValueError("Field must be a string or None")
        self.field = field

    def __repr__(self):
        return f"Get_host_info(field={self.field})"

    async def _get_host_info_callback(self, host_info, global_info, command, cp, caller):
        return host_info.get(self.field)

    def execute(self):
        r = yield Command(
            f"{self}",
            local=True,
            callback=self._get_host_info_callback,
            caller=self
        )