import asyncssh
from reemote.operation import Operation
from reemote.result import Result


class Isdir:
    """
    A class to encapsulate the functionality of checking if a path refers to a directory
    using SFTP isdir in Unix-like operating systems.

    Attributes:
        hosts (list): The list of hosts on which to check the directory.
        path: The remote path to check (can be PurePath, str, or bytes).

    **Examples:**

    .. code:: python

        yield Isdir(
            hosts=["10.156.135.16", "10.156.135.17"],
            path="/path/to/directory"
        )

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution. The boolean result for each
        host will be returned in the operation result.

    Notes:
        If hosts is None or empty, the operation will execute on the current host.
        The path must be a valid remote path accessible via SFTP.
    """

    def __init__(self, hosts: list = None, path=None):
        self.hosts = hosts
        self.path = path

    def __repr__(self):
        return f"Isdir(hosts={self.hosts!r}, path={self.path!r})"

    @staticmethod
    async def _isdir_callback(host_info, global_info, command, cp, caller):
        """Static callback method for checking if a path is a directory"""
        # Check if this host is in the target hosts list or if hosts list is empty/None
        if (caller.hosts is None or
                not caller.hosts or
                host_info["host"] in caller.hosts):

            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    # Check if the path refers to a directory
                    if caller.path:
                        is_dir = await sftp.isdir(caller.path)
                        return is_dir
                    else:
                        raise ValueError("Path must be provided for isdir operation")
        else:
            # If host is not in the target list, return None to skip execution
            return None

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._isdir_callback, caller=self)
        r.executed = True
        r.changed = False
        return r