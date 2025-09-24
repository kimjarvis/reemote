import asyncssh
from reemote.operation import Operation
class Mkdir:
    """
    A class to encapsulate the functionality of mkdir in Unix-like operating systems.

    Attributes:
        path (str): The directory path to create.
        attrs (str): asyncssh SFTPAttrs object for directory attributes.

    **Examples:**

    .. code:: python

        yield Mkdir(
            path='/home/user/hfs',
            attrs=SFTPAttrs(permissions=0o755),
        )

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        If hosts is None or empty, the operation will execute on the current host.
    """

    def __init__(self,
                 path: str,
                 attrs: str=None,
                 ):
        self.path = path
        self.attrs = attrs

    def __repr__(self):
        return (f"Mkdir(path={self.path!r}, "
                f"attrs={self.attrs!r}, "
                ")")

    @staticmethod
    async def _mkdir_callback(host_info, global_info, command, cp, caller):
        print(f"{caller}")
        """Static callback method for directory creation"""
        # print(f"Making directory on host {host_info['host']}")

        # Validate host_info
        required_keys = ['host', 'username', 'password']
        for key in required_keys:
            if key not in host_info or host_info[key] is None:
                raise ValueError(f"Missing or invalid value for '{key}' in host_info.")

        # Validate caller attributes
        if caller.path is None:
            raise ValueError("The 'path' attribute of the caller cannot be None.")
        if caller.attrs is None:
            raise ValueError("The 'attrs' attribute of the caller cannot be None.")

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