import asyncssh
from reemote.operation import Operation



class Get_statvfs:
    """
    A class to encapsulate the functionality of getting filesystem statistics
    using SFTP statvfs (stat on a filesystem path) in Unix-like operating systems.

    Attributes:
        hosts (list): The list of hosts from which to get filesystem statistics.
        path (str): The filesystem path to get statistics for.

    **Examples:**
    .. code:: python

        yield Get_statvfs(
            hosts=["10.156.135.16", "10.156.135.17"],
            path="/home/user"
        )

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution. The filesystem statistics for each
        host will be returned in the operation result.

    Notes:
        If hosts is None or empty, the operation will execute on the current host.
        The path must be a valid filesystem path accessible via SFTP.
    """

    def __init__(self, hosts: list = None, path: str = None):
        self.hosts = hosts
        self.path = path

    def __repr__(self):
        return f"Get_statvfs(hosts={self.hosts!r}, path={self.path!r})"



    @staticmethod
    async def _getstatvfs_callback(host_info, global_info, command, cp, caller):
        """Static callback method for getting filesystem statistics from a path"""

        # Check if this host is in the target hosts list or if hosts list is empty/None
        if (caller.hosts is None or
                not caller.hosts or
                host_info["host"] in caller.hosts):

            async def run_client():
                try:
                    async with asyncssh.connect(**host_info) as conn:
                        async with conn.start_sftp_client() as sftp:
                            # Get filesystem statistics using statvfs on the path
                            if caller.path:
                                # Convert path to bytes as required by asyncssh statvfs
                                path_bytes = caller.path.encode('utf-8')
                                vfs_attrs = await sftp.statvfs(path_bytes)
                                return vfs_attrs
                            else:
                                raise ValueError("Path must be provided for statvfs operation")
                except (OSError, asyncssh.Error) as exc:
                    print(f'SFTP statvfs operation failed on {host_info["host"]}: {str(exc)}')
                    raise

            try:
                vfs_attrs = await run_client()
                return vfs_attrs
            except Exception as e:
                print(f"An error occurred on {host_info['host']}: {e}")
                return None

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._getstatvfs_callback, caller=self)
        r.executed = True
        r.changed = False
        return r