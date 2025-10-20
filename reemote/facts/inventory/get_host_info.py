# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command

class Get_host_info:
    """Represents a command to retrieve specific information about the host.

    This class is designed to be used within the reemote framework. It
    constructs a local command that accesses the [host_info](file:///home/kim/reemote/reemote/command.py#L34-L34) dictionary
    provided by the reemote runner. An instance of this class is a
    generator-based task that, when executed, yields a [Command](file:///home/kim/reemote/reemote/command.py#L5-L56) object.

    Args:
        field (str, optional): The key of the information to retrieve from the
            host's info dictionary. If `None`, the behavior depends on the
            implementation. Defaults to None.

    Raises:
        ValueError: If the provided [field](file:///home/kim/reemote/reemote/facts/server/get_os.py#L2-L2) is not a string or None.

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