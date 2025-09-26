import asyncssh
from reemote.operation import Operation


class Chdir:
    """
    A class to encapsulate the functionality of chdir (change directory) in
    Unix-like operating systems.

    Attributes:
        path (str): The directory path to change to.

    **Examples:**

    .. code:: python

        yield Chdir(path='/home/user/hfs')

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution.
    """

    def __init__(self, path: str):
        self.path = path

    def __repr__(self):
        return f"Chdir(path={self.path!r})"

    @staticmethod
    async def _chdir_callback(host_info, global_info, command, cp, caller):
        """Static callback method for directory change"""

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
                    # Change the current remote working directory
                    await sftp.chdir(path=caller.path)
                    return f"Successfully changed directory to {caller.path} on {host_info['host']}"
        except (OSError, asyncssh.Error) as exc:
            raise  # Re-raise the exception to handle it in the caller

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._chdir_callback, caller=self)
        r.executed = True
        r.changed = False
        return r