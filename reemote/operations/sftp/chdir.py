import asyncssh
from reemote.operation import Operation


class Chdir:
    """
    A class to encapsulate the functionality of chdir (change directory) in
    Unix-like operating systems.

    Attributes:
        path (str): The directory path to change to.
        hosts (list): The list of hosts on which the directory change is to be performed.

    **Examples:**

    .. code:: python

        yield Chdir(
            path='/home/user/hfs',
            hosts=["10.156.135.16", "10.156.135.17"]
        )

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution.

    Notes:
        If hosts is None or empty, the operation will execute on the current host.
    """

    def __init__(self, path: str, hosts: list = None):
        self.path = path
        self.hosts = hosts

    def __repr__(self):
        return f"Chdir(path={self.path!r}, hosts={self.hosts!r})"

    @staticmethod
    async def _chdir_callback(host_info, global_info, command, cp, caller):
        print(f"{caller}")
        """Static callback method for directory change"""

        # Check if this host is in the target hosts list or if hosts list is empty/None
        if (caller.hosts is None or
                not caller.hosts or
                host_info["host"] in caller.hosts):

            async def run_client():
                try:
                    async with asyncssh.connect(**host_info) as conn:
                        async with conn.start_sftp_client() as sftp:
                            # Change the current remote working directory
                            await sftp.chdir(path=caller.path)
                except (OSError, asyncssh.Error) as exc:
                    print(f'SFTP operation failed on {host_info["host"]}: {str(exc)}')
                    raise

            try:
                await run_client()
            except Exception as e:
                print(f"An error occurred on {host_info['host']}: {e}")
                return None

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._chdir_callback, caller=self)
        r.executed = True
        r.changed = False