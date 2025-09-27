import asyncssh
from reemote.operation import Operation


class Exists:
    """
    A class to encapsulate the functionality of checking if a remote path exists
    using SFTP exists method in Unix-like operating systems.

    Attributes:
        path (str): The remote path to check for existence.

    **Examples:**

    .. code:: python

        yield Exists(path="/path/to/check")

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution. The existence check result for each
        host will be returned in the operation result.

    Notes:
        The operation will execute on all hosts in the current execution context.
        The path must be a valid remote path accessible via SFTP.
    """

    def __init__(self, path: str = None):
        self.path = path

    def __repr__(self):
        return f"Exists(path={self.path!r})"

    @staticmethod
    async def _exists_callback(host_info, global_info, command, cp, caller):
        """Static callback method for checking if a remote path exists"""
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                # Check if path exists using the exists method
                if caller.path:
                    exists = await sftp.exists(caller.path)
                    return exists
                else:
                    raise ValueError("Path must be provided for exists operation")

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._exists_callback, caller=self)
        r.executed = True
        r.changed = False
        return r