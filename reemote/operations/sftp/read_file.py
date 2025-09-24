import asyncssh
from reemote.operation import Operation
class Read_file:
    """
    A class to encapsulate the functionality of reading files in Unix-like operating systems.
    It allows users to specify a target file to be read from multiple hosts.
    The content of the file is available as stdout.

    Attributes:
        path (str): The file path to read from.
        hosts (list): The list of hosts from which the file is being read.

    **Examples:**

    .. code:: python

        # Get file content from specific hosts
        r = yield Read_file(path='example.txt', hosts=['192.168.122.5', '192.168.122.6'])
        # The content is available in stdout
        print(r.cp.stdout)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        If hosts is None or empty, the operation will execute on the current host.
    """

    def __init__(self,
                 path: str,
                 hosts: list = None):
        self.path = path
        self.hosts = hosts

    def __repr__(self):
        return (f"Read_file(path={self.path!r}, "
                f"hosts={self.hosts!r})")

    @staticmethod
    async def _read_file_callback(host_info, global_info, command, cp, caller):
        print(f"{caller}")
        """Static callback method for file reading"""
        # Initialize file_content to None
        file_content = None

        # Check if this host is in the target hosts list or if hosts list is empty/None
        if (caller.hosts is None or
                not caller.hosts or
                host_info["host"] in caller.hosts):

            print(f"Reading file from host {host_info['host']}")

            async def run_client():
                nonlocal file_content  # Use nonlocal to modify the outer variable
                try:
                    async with asyncssh.connect(**host_info) as conn:
                        async with conn.start_sftp_client() as sftp:
                            # Open the remote file and read its contents
                            async with sftp.open(caller.path, 'r') as remote_file:
                                file_content = await remote_file.read()
                            print(f"Successfully read file from {host_info['host']}")
                except (OSError, asyncssh.Error) as exc:
                    print(f'SFTP operation failed on {host_info["host"]}: {str(exc)}')
                    raise  # Re-raise the exception to handle it in the caller

            try:
                await run_client()
            except Exception as e:
                print(f"An error occurred on {host_info['host']}: {e}")
                return None  # Return None or handle the error as needed

            return file_content  # Return the actual file content

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._read_file_callback, caller=self)
        r.executed = True
        r.changed = False