# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import asyncssh
from reemote.command import Command
from reemote.result import Result


class Isfile:
    """
    A class to encapsulate the functionality of checking if a path refers to a regular builtin
    using SFTP isfile in Unix-like operating systems.

    Attributes:
        path: The remote path to check (can be PurePath, str, or bytes).

    **Examples:**

    .. code:: python

        yield Isfile(path="/path/to/builtin.txt")

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution. The boolean result for each
        host will be returned in the operation result.

    Notes:
        The operation will execute on all hosts in the current execution context.
        The path must be a valid remote path accessible via SFTP.
    """

    def __init__(self, path=None):
        self.path = path

    def __repr__(self):
        return f"Isfile(path={self.path!r})"

    @staticmethod
    async def _isfile_callback(host_info, global_info, command, cp, caller):
        """Static callback method for checking if a path is a regular builtin"""
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                # Check if the path refers to a regular builtin
                if caller.path:
                    is_file = await sftp.isfile(caller.path)
                    return is_file
                else:
                    raise ValueError("Path must be provided for isfile operation")

    def execute(self):
        r = yield Command(f"{self}", local=True, callback=self._isfile_callback, caller=self)
        r.executed = True
        r.changed = False
        return r
