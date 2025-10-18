# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import asyncssh
from reemote.command import Command


class Chmod:
    """
    A class to encapsulate the functionality of chmod (change builtin mode) in
    Unix-like operating systems.

    Attributes:
        path (str): The builtin or directory path to change permissions for.
        mode (int): The new builtin permissions, expressed as an octal integer (e.g., 0o755).
        follow_symlinks (bool): Whether or not to follow symbolic links (default: True).

    **Examples:**

    .. code:: python

        yield Chmod(
            path='/home/user/script.sh',
            mode=0o755,
        )

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution.
    """

    def __init__(self, path: str, mode: int, follow_symlinks: bool = True):
        self.path = path
        self.mode = mode
        self.follow_symlinks = follow_symlinks

    def __repr__(self):
        return f"Chmod(path={self.path!r}, mode={oct(self.mode)!r}, follow_symlinks={self.follow_symlinks!r})"

    @staticmethod
    async def _chmod_callback(host_info, global_info, command, cp, caller):
        """Static callback method for builtin permission change"""

        # Validate host_info
        required_keys = ['host', 'username', 'password']
        for key in required_keys:
            if key not in host_info or host_info[key] is None:
                raise ValueError(f"Missing or invalid value for '{key}' in host_info.")

        # Validate caller attributes
        if caller.path is None:
            raise ValueError("The 'path' attribute of the caller cannot be None.")
        if caller.mode is None:
            raise ValueError("The 'mode' attribute of the caller cannot be None.")

        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    # Change the permissions of the remote builtin/directory
                    await sftp.chmod(
                        path=caller.path,
                        mode=caller.mode,
                        follow_symlinks=caller.follow_symlinks
                    )
                    return f"Changed permissions of {caller.path} to {oct(caller.mode)} on {host_info['host']}"
        except (OSError, asyncssh.Error) as exc:
            raise  # Re-raise the exception to handle it in the caller

    def execute(self):
        r = yield Command(f"{self}", local=True, callback=self._chmod_callback, caller=self)
        r.executed = True
        r.changed = True  # Set to True since chmod typically changes the system state
        return r
