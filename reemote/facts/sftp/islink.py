import asyncssh
from reemote.operation import Operation


class Islink:
    """
    A class to encapsulate the functionality of checking if a path refers to a symbolic link
    using SFTP islink in Unix-like operating systems.
    Attributes:
        hosts (list): The list of hosts on which to check the symbolic link.
        path: The remote path to check (can be PurePath, str, or bytes).

    **Examples:**

    .. code:: python

        yield Get_islink(
            hosts=["10.156.135.16", "10.156.135.17"],
            path="/path/to/symlink"
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
        return f"Get_islink(hosts={self.hosts!r}, path={self.path!r})"

    @staticmethod
    async def _islink_callback(host_info, global_info, command, cp, caller):
        """Static callback method for checking if a path is a symbolic link"""

        # Check if this host is in the target hosts list or if hosts list is empty/None
        if (caller.hosts is None or
                not caller.hosts or
                host_info["host"] in caller.hosts):

            async def run_client():
                try:
                    async with asyncssh.connect(**host_info) as conn:
                        async with conn.start_sftp_client() as sftp:
                            # Check if the path refers to a symbolic link
                            if caller.path:
                                is_link = await sftp.islink(caller.path)
                                return is_link
                            else:
                                raise ValueError("Path must be provided for islink operation")
                except (OSError, asyncssh.Error) as exc:
                    print(f'SFTP islink operation failed on {host_info["host"]}: {str(exc)}')
                    raise

            try:
                is_link_result = await run_client()
                return is_link_result
            except Exception as e:
                print(f"An error occurred on {host_info['host']}: {e}")
                return None

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._islink_callback, caller=self)
        r.executed = True
        r.changed = False
        return r