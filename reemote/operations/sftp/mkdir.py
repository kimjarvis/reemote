import asyncssh
from reemote.operation import Operation


class Mkdir:
    """
    A class to encapsulate the functionality of mkdir in Unix-like operating systems.

    Attributes:
        path (str): The directory path to create.
        attrs (SFTPAttrs, optional): SFTP attributes to set on the created directory.

    **Examples:**

    .. code:: python

        yield Mkdir(path='/home/user/hfs')
        yield Mkdir(path='/home/user/hfs', attrs=SFTPAttrs(permissions=0o755))

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.
    """

    def __init__(self, path: str, attrs: asyncssh.SFTPAttrs = None):
        self.path = path
        self.attrs = attrs

    def __repr__(self):
        if self.attrs:
            return f"Mkdir(path={self.path!r}, attrs={self.attrs!r})"
        return f"Mkdir(path={self.path!r})"

    @staticmethod
    async def _mkdir_callback(host_info, global_info, command, cp, caller):
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
                    # Create the remote directory with optional attributes
                    await sftp.mkdir(path=caller.path, attrs=caller.attrs)

                    # If attributes were provided, we should check if they were applied
                    # and set changed flag accordingly
                    if caller.attrs:
                        # Verify the attributes were set by reading them back
                        stat_result = await sftp.stat(caller.path)
                        # Note: We can't easily determine if all attributes were applied
                        # but we'll assume the operation changed something if attrs were provided
                        return f"Successfully created directory {caller.path} with attributes on {host_info['host']}"
                    else:
                        return f"Successfully created directory {caller.path} on {host_info['host']}"

        except (OSError, asyncssh.Error) as exc:
            raise  # Re-raise the exception to handle it in the caller

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._mkdir_callback, caller=self)
        r.executed = True
        # Set changed to True if attributes were provided, False otherwise
        r.changed = self.attrs is not None
        return r