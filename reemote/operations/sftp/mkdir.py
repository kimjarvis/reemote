import asyncssh
from reemote.operation import Operation
class Mkdir:
    """
    A class to encapsulate the functionality of mkdir in Unix-like operating systems.

    Attributes:
        path (str): The directory path to create.
        attrs (str): asyncssh SFTPAttrs object for directory attributes.
        hosts (list): The list of hosts on which the directory is to be created.

    **Examples:**

    .. code:: python

        class Mkdir_example:
            def execute(self):
                yield Mkdir(path='/home/user/hfs',
                          attrs=SFTPAttrs(permissions=0o755),
                          hosts=["10.156.135.16", "10.156.135.17"])

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        If hosts is None or empty, the operation will execute on the current host.
    """

    def __init__(self,
                 path: str,
                 attrs: str,
                 hosts: list = None):
        self.path = path
        self.attrs = attrs
        self.hosts = hosts

    def __repr__(self):
        return (f"Mkdir(path={self.path!r}, "
                f"attrs={self.attrs!r}, "
                f"hosts={self.hosts!r})")

    @staticmethod
    async def _mkdir_callback(host_info, global_info, command, cp, caller):
        """Static callback method for directory creation"""
        # print("Making directory")

        # Check if this host is in the target hosts list or if hosts list is empty/None
        if (caller.hosts is None or
                not caller.hosts or
                host_info["host"] in caller.hosts):

            # print(f"Making directory on host {host_info['host']}")

            async def run_client():
                # print(f"Connecting to {host_info['host']}")
                try:
                    async with asyncssh.connect(**host_info) as conn:
                        # print(f"Connected to {host_info['host']}")
                        async with conn.start_sftp_client() as sftp:
                            # print(f"Creating directory {caller.path} on {host_info['host']}")
                            # Create the directory with the desired attributes
                            await sftp.mkdir(path=caller.path, attrs=caller.attrs)
                            # print(f"Successfully created directory on {host_info['host']}")
                except (OSError, asyncssh.Error) as exc:
                    print(f'SFTP operation failed on {host_info["host"]}: {str(exc)}')
                    raise  # Re-raise the exception to handle it in the caller

            try:
                await run_client()
            except Exception as e:
                print(f"An error occurred on {host_info['host']}: {e}")
                return None  # Return None or handle the error as needed

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._mkdir_callback, caller=self)
        r.executed = True
        r.changed = False