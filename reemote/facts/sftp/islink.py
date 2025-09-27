import asyncssh
from reemote.operation import Operation


class Islink:
    """
    A class to encapsulate the functionality of checking if a path refers to a symbolic link
    using SFTP islink in Unix-like operating systems.

    Attributes:
        path: The remote path to check (can be PurePath, str, or bytes).

    **Examples:**

    .. code:: python

        yield Islink(path="/path/to/symlink")

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
        return f"Islink(path={self.path!r})"

    @staticmethod
    async def _islink_callback(host_info, global_info, command, cp, caller):
        """Static callback method for checking if a path is a symbolic link"""
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                # Check if the path refers to a symbolic link
                if caller.path:
                    is_link = await sftp.islink(caller.path)
                    return is_link
                else:
                    raise ValueError("Path must be provided for islink operation")

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._islink_callback, caller=self)
        r.executed = True
        r.changed = False
        return r