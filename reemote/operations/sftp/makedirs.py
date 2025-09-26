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

    **Examples:**

    .. code:: python

        yield Makedirs(path='/home/user/hfs/subdir1/subdir2',
             attrs=SFTPAttrs(permissions=0o755),
             exist_ok=True,
        )

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        This will create all intermediate directories in the path if they don't exist.
    """

    @staticmethod
    async def _makedirs_callback(host_info, global_info, command, cp, caller):
        """Static callback method for directory creation"""

        # Validate host_info
        required_keys = ['host', 'username', 'password']
        for key in required_keys:
            if key not in host_info or host_info[key] is None:
                raise ValueError(f"Missing or invalid value for '{key}' in host_info.")

        # Validate caller attributes
        if caller.path is None:
            raise ValueError("The 'path' attribute of the caller cannot be None.")

        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    # Create the remote directory recursively
                    await sftp.makedirs(path=caller.path, attrs=caller.attrs, exist_ok=caller.exist_ok)
                    return f"Successfully created directory {caller.path} on {host_info['host']}"
        except (OSError, asyncssh.Error) as exc:
            raise  # Re-raise the exception to handle it in the caller

    def __init__(self,
                 path: str,
                 attrs: SFTPAttrs = None,
                 exist_ok: bool = False):
        self.path = path

        # Set default SFTPAttrs if none provided
        self.attrs = attrs if attrs is not None else SFTPAttrs()
        self.exist_ok = exist_ok

    def __repr__(self):
        return (f"Makedirs(path={self.path!r}, "
                f"attrs={self.attrs!r}, "
                f"exist_ok={self.exist_ok!r})")

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._makedirs_callback, caller=self)
        r.executed = True
        r.changed = False
        return r