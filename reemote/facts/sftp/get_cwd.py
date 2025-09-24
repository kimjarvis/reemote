import asyncssh
from reemote.operation import Operation


class Get_cwd:
    """
    A class to encapsulate the functionality of cwd (get current working directory)
    in Unix-like operating systems.

    Attributes:
        hosts (list): The list of hosts from which to get the current working directory.

    **Examples:**

    .. code:: python

        yield Getcwd(
            hosts=["10.156.135.16", "10.156.135.17"]
        )

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution. The current working directory for each
        host will be returned in the operation result.

    Notes:
        If hosts is None or empty, the operation will execute on the current host.
    """

    def __init__(self, hosts: list = None):
        self.hosts = hosts

    def __repr__(self):
        return f"Getcwd(hosts={self.hosts!r})"

    @staticmethod
    async def _getcwd_callback(host_info, global_info, command, cp, caller):
        """Static callback method for getting current working directory"""

        # Check if this host is in the target hosts list or if hosts list is empty/None
        if (caller.hosts is None or
                not caller.hosts or
                host_info["host"] in caller.hosts):

            async def run_client():
                try:
                    async with asyncssh.connect(**host_info) as conn:
                        async with conn.start_sftp_client() as sftp:
                            # Get the current remote working directory
                            cwd = await sftp.getcwd()
                            return cwd
                except (OSError, asyncssh.Error) as exc:
                    print(f'SFTP operation failed on {host_info["host"]}: {str(exc)}')
                    raise

            try:
                cwd = await run_client()
                return cwd
            except Exception as e:
                print(f"An error occurred on {host_info['host']}: {e}")
                return None

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._getcwd_callback, caller=self)
        r.executed = True
        r.changed = False
        return r