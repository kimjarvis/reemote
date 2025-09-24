import asyncssh
from asyncssh import SFTPAttrs
from reemote.operation import Operation


class Makedirs:
    """
    A class to encapsulate the functionality of makedirs (recursive directory creation).

    Attributes:
        path (str): The directory path to create recursively.
        attrs (SFTPAttrs): asyncssh SFTPAttrs object for directory attributes.
        exist_ok (bool): Whether to raise an error if the target directory already exists.
        hosts (list): The list of hosts on which the directories are to be created.

    **Examples:**

    .. code:: python

        yield Makedirs(path='/home/user/hfs/subdir1/subdir2',
             attrs=SFTPAttrs(permissions=0o755),
             exist_ok=True,
             hosts=["10.156.135.16", "10.156.135.17"],
        )

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        This will create all intermediate directories in the path if they don't exist.
        If hosts is None or empty, the operation will execute on the current host.
    """

    @staticmethod
    async def _makedirs_callback(host_info, global_info, command, cp, caller):
        print(f"{caller}")
        """Static callback method for directory creation"""
        # print("Making directories recursively")

        # Check if this host is in the target hosts list or if hosts list is empty/None
        if (caller.hosts is None or
                not caller.hosts or
                host_info["host"] in caller.hosts):

            # print(f"Making directories on host {host_info['host']}")

            async def run_client():
                # print(f"Connecting to {host_info['host']}")
                try:
                    async with asyncssh.connect(**host_info) as conn:
                        # print(f"Connected to {host_info['host']}")
                        async with conn.start_sftp_client() as sftp:
                            # print(f"Creating directory {caller.path} on {host_info['host']}")
                            await sftp.makedirs(path=caller.path, attrs=caller.attrs, exist_ok=caller.exist_ok)
                            # print(f"Successfully created directory on {host_info['host']}")
                except (OSError, asyncssh.Error) as exc:
                    print(f'SFTP makedirs operation failed on {host_info["host"]}: {str(exc)}')
                    raise

            try:
                await run_client()
            except Exception as e:
                print(f"An error occurred on {host_info['host']}: {e}")
                return None

    def __init__(self,
                 path: str,
                 attrs: SFTPAttrs = None,
                 exist_ok: bool = False,
                 hosts: list = None):
        self.path = path

        # Set default SFTPAttrs if none provided
        self.attrs = attrs if attrs is not None else SFTPAttrs()
        self.exist_ok = exist_ok
        self.hosts = hosts

    def __repr__(self):
        return (f"Makedirs(path={self.path!r}, "
                f"attrs={self.attrs!r}, "
                f"exist_ok={self.exist_ok!r}, "
                f"hosts={self.hosts!r})")

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._makedirs_callback, caller=self)
        r.executed = True
        r.changed = False