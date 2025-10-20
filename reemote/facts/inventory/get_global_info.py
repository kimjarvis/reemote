# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command

class Get_global_info:
    """Represents a command to retrieve information from a global context.

    This class is used to create a command that fetches data from a
    [global_info](file:///home/kim/reemote/reemote/command.py#L35-L35) dictionary available in the execution environment. When
    executed, it can retrieve a specific value by its key ([field](file:///home/kim/reemote/reemote/facts/server/get_os.py#L2-L2)) or
    the entire dictionary if no field is specified.

    Args:
        field (str, optional): The key of the value to retrieve from the
            global information dictionary. If `None`, the entire
            dictionary is returned. Defaults to None.

    Raises:
        ValueError: If the provided [field](file:///home/kim/reemote/reemote/facts/server/get_os.py#L2-L2) is not a string or None.

    Attributes:
        field (str | None): The key of the value to retrieve from the
            global information dictionary.
    """

    def __init__(self, field=None):
        if field is not None and not isinstance(field, str):
            raise ValueError("Field must be a string or None")
        self.field = field

    def __repr__(self):
        return f"Get_host_info(field={self.field})"

    async def _get_global_info_callback(self, host_info, global_info, command, cp, caller):
        return global_info.get(self.field)

    def execute(self):
        r = yield Command(
            f"{self}",
            local=True,
            callback=self._get_global_info_callback,
            caller=self
        )