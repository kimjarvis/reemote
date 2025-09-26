import asyncssh
from reemote.operation import Operation
from reemote.result import Result

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
                 # attrs: asyncssh.SFTPAttrs = None,
                 ):
        self.path = path
        # self.attrs = attrs

    def __repr__(self):
        return (f"Mkdir(path={self.path!r})")
        # return (f"Mkdir(path={self.path!r}, attrs={self.attrs!r})")

    @staticmethod
    async def _mkdir_callback(host_info, global_info, command, cp, caller):
        """Static callback method for directory creation"""
        # print(f"{caller}")

        # Validate host_info
        required_keys = ['host', 'username', 'password']
        for key in required_keys:
            if key not in host_info or host_info[key] is None:
                raise ValueError(f"Missing or invalid value for '{key}' in host_info.")

        # Validate caller attributes
        if caller.path is None:
            raise ValueError("The 'path' attribute of the caller cannot be None.")

        async def run_client():
            try:
                async with asyncssh.connect(**host_info) as conn:
                    async with conn.start_sftp_client() as sftp:
                        await sftp.mkdir(path=caller.path)
                        # Return success message instead of None
                        return f"Successfully created directory {caller.path} on {host_info['host']}"
            except (OSError, asyncssh.Error) as exc:
                error_msg = f'SFTP operation failed on {host_info["host"]}: {str(exc)}'
                # print(error_msg)
                raise  # Re-raise the exception to handle it in the caller

        try:
            result = await run_client()
            return result  # Return the success message
        except Exception as e:
            error_msg = f"An error occurred on {host_info['host']}: {e}"
            # print(error_msg)
            raise  # Re-raise to be caught by the framework

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._mkdir_callback, caller=self)
        r.executed = True
        r.changed = False
        return r