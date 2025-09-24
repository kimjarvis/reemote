import asyncssh
from reemote.operation import Operation


class Remove:
    """
    A class to encapsulate the functionality of remove (rm) in Unix-like operating systems.

    Attributes:
        path (str): The file path to remove.
        hosts (list): The list of hosts on which the file is to be removed.

    **Examples:**

    .. code:: python

        yield Remove(
            path='/home/user/unwanted_file.txt',
            hosts=["10.156.135.16", "10.156.135.17"]
        )

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        If hosts is None or empty, the operation will execute on the current host.
    """

    def __init__(self, path: str, hosts: list = None):
        self.path = path
        self.hosts = hosts

    def __repr__(self):
        return (f"Remove(path={self.path!r}, "
                f"hosts={self.hosts!r})")

    @staticmethod
    async def _remove_callback(host_info, global_info, command, cp, caller):
        print(f"{caller}")
        """Static callback method for file removal"""
        # print("Removing file")

        # Check if this host is in the target hosts list or if hosts list is empty/None
        if (caller.hosts is None or
                not caller.hosts or
                host_info["host"] in caller.hosts):

            # print(f"Removing file on host {host_info['host']}")

            async def run_client():
                # print(f"Connecting to {host_info['host']}")
                try:
                    async with asyncssh.connect(**host_info) as conn:
                        # print(f"Connected to {host_info['host']}")
                        async with conn.start_sftp_client() as sftp:
                            # print(f"Removing file {caller.path} on {host_info['host']}")
                            # Remove the file
                            await sftp.remove(path=caller.path)
                            # print(f"Successfully removed file on {host_info['host']}")
                except (OSError, asyncssh.Error) as exc:
                    print(f'SFTP remove operation failed on {host_info["host"]}: {str(exc)}')
                    raise  # Re-raise the exception to handle it in the caller

            try:
                await run_client()
            except Exception as e:
                print(f"An error occurred on {host_info['host']}: {e}")
                return None  # Return None or handle the error as needed

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._remove_callback, caller=self)
        r.executed = True
        r.changed = False