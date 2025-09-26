import asyncssh
from reemote.operation import Operation


class Rename:
    """
    A class to encapsulate the functionality of renaming files/directories
    in Unix-like operating systems using SFTP.

    Attributes:
        oldpath (str): The current path of the file/directory to rename.
        newpath (str): The new path for the file/directory.
        flags (int, optional): Flags to control rename behavior (SFTPv5+ only).
            Common flags include:
            - 0x0001: OVERWRITE - Allow overwriting existing files
            - 0x0002: ATOMIC - Perform atomic rename
            - 0x0004: NATIVE - Use native filesystem semantics

    **Examples:**

    .. code:: python

        yield Rename(
            oldpath='/home/user/oldname.txt',
            newpath='/home/user/newname.txt'
        )

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution.

    Notes:
        The flags parameter is only supported in SFTP version 5 and later.
        For older SFTP versions, only basic rename functionality is available.
    """

    def __init__(self, oldpath: str, newpath: str, flags: int = 0):
        self.oldpath = oldpath
        self.newpath = newpath
        self.flags = flags

    def __repr__(self):
        return f"Rename(oldpath={self.oldpath!r}, newpath={self.newpath!r}, flags={self.flags!r})"

    @staticmethod
    async def _rename_callback(host_info, global_info, command, cp, caller):
        """Static callback method for file/directory rename"""

        # Validate host_info (matching Read_file error handling)
        required_keys = ['host', 'username', 'password']
        for key in required_keys:
            if key not in host_info or host_info[key] is None:
                raise ValueError(f"Missing or invalid value for '{key}' in host_info.")

        # Validate caller attributes (matching Read_file error handling)
        if caller.oldpath is None:
            raise ValueError("The 'oldpath' attribute of the caller cannot be None.")
        if caller.newpath is None:
            raise ValueError("The 'newpath' attribute of the caller cannot be None.")

        try:
            # Connect to the SSH server
            async with asyncssh.connect(**host_info) as conn:
                # Start an SFTP session
                async with conn.start_sftp_client() as sftp:
                    # Rename the remote file/directory
                    await sftp.rename(caller.oldpath, caller.newpath, caller.flags)
        except (OSError, asyncssh.Error) as exc:
            raise  # Re-raise the exception to handle it in the caller

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._rename_callback, caller=self)
        r.executed = True
        r.changed = False
        return r