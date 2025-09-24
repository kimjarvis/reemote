import asyncssh
from reemote.operation import Operation


class Check_exists:
    """
    A class to encapsulate the functionality of checking if a remote path exists
    using SFTP exists method in Unix-like operating systems.

    Attributes:
        hosts (list): The list of hosts on which to check path existence.
        path (str): The remote path to check for existence.

    **Examples:**

    .. code:: python

        yield Check_exists(
            hosts=["10.156.135.16", "10.156.135.17"],
            path="/path/to/check"
        )

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution. The existence check result for each
        host will be returned in the operation result.

    Notes:
        If hosts is None or empty, the operation will execute on the current host.
        The path must be a valid remote path accessible via SFTP.
    """

    def __init__(self, hosts: list = None, path: str = None):
        self.hosts = hosts
        self.path = path

    def __repr__(self):
        return f"Check_exists(hosts={self.hosts!r}, path={self.path!r})"

    @staticmethod
    async def _check_exists_callback(host_info, global_info, command, cp, caller):
        """Static callback method for checking if a remote path exists"""

        # Check if this host is in the target hosts list or if hosts list is empty/None
        if (caller.hosts is None or
                not caller.hosts or
                host_info["host"] in caller.hosts):

            async def run_client():
                try:
                    async with asyncssh.connect(**host_info) as conn:
                        async with conn.start_sftp_client() as sftp:
                            # Check if path exists using the exists method
                            if caller.path:
                                exists = await sftp.exists(caller.path)
                                return exists
                            else:
                                raise ValueError("Path must be provided for exists operation")
                except (OSError, asyncssh.Error) as exc:
                    print(f'SFTP exists operation failed on {host_info["host"]}: {str(exc)}')
                    raise

            try:
                exists_result = await run_client()
                return exists_result
            except Exception as e:
                print(f"An error occurred on {host_info['host']}: {e}")
                return False

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._check_exists_callback, caller=self)
        r.executed = True
        r.changed = False
        return r