# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import asyncssh
from reemote.command import Command


class Get_fstat:
    """
    A class to encapsulate the functionality of getting builtin attributes
    using SFTP fstat (stat on an open builtin handle) in Unix-like operating systems.

    Attributes:
        file_handle: The open builtin handle to get attributes for.
        flags (int): Flags indicating attributes of interest (SFTPv4 or later)

    **Examples:**

    .. code:: python

        yield Get_fstat(file_handle=file_handle)

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution. The builtin attributes for each
        host will be returned in the operation result.

    Notes:
        The operation will execute on all hosts in the current execution context.
        The builtin handle must be an open SFTP builtin handle obtained from a previous operation.
    """

    def __init__(self, file_handle=None, flags: int = None):
        self.file_handle = file_handle
        self.flags = flags

    def __repr__(self):
        return f"Get_fstat(file_handle={self.file_handle!r}, flags={self.flags!r})"

    @staticmethod
    async def _getfstat_callback(host_info, global_info, command, cp, caller):
        """Static callback method for getting builtin attributes from an open builtin handle"""
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                # Get builtin attributes using fstat on the open builtin handle
                if caller.file_handle:
                    if caller.flags:
                        file_attrs = await caller.file_handle.fstat(caller.flags)
                    else:
                        file_attrs = await caller.file_handle.fstat()
                    return file_attrs
                else:
                    raise ValueError("File handle must be provided for fstat operation")

    def execute(self):
        r = yield Command(f"{self}", local=True, callback=self._getfstat_callback, caller=self)
        r.executed = True
        r.changed = False
        return r
