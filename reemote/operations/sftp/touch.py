# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import asyncssh
from reemote.command import Command
from typing import List, Tuple, Dict, Any, Union, Optional

class Touch:
    """
    A class to encapsulate the functionality of touch (create builtin) in
    Unix-like operating systems using asyncssh's SFTP open method.

    Attributes:
        path (str): The builtin path to create.
        pflags_or_mode (Union[int, str]): The access mode to use for the remote builtin.
        attrs (SFTPAttrs): File attributes to use when creating the builtin.
        encoding (Optional[str]): The Unicode encoding to use (default: 'utf-8').
        errors (str): Error-handling mode for Unicode (default: 'strict').
        block_size (int): Block size for read/write requests (default: -1).
        max_requests (int): Maximum parallel read/write requests (default: -1).

    **Examples:**

    .. code:: python

        yield Touch(
            path='/home/user/newfile.txt',
            pflags_or_mode='w',  # Create builtin if it doesn't exist
            attrs=asyncssh.SFTPAttrs(perms=0o644)  # Set builtin permissions
        )

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution.

    Notes:
        The builtin is created but no data is written to it.
    """

    def __init__(self, path: str,
                 pflags_or_mode: Union[int, str] = asyncssh.FXF_WRITE | asyncssh.FXF_CREAT,
                 attrs: asyncssh.SFTPAttrs = asyncssh.SFTPAttrs(),
                 encoding: Optional[str] = 'utf-8',
                 errors: str = 'strict',
                 block_size: int = -1,
                 max_requests: int = -1):
        self.path = path
        self.pflags_or_mode = pflags_or_mode
        self.attrs = attrs
        self.encoding = encoding
        self.errors = errors
        self.block_size = block_size
        self.max_requests = max_requests

    def __repr__(self):
        return (f"Touch(path={self.path!r}, "
                f"pflags_or_mode={self.pflags_or_mode!r}, attrs={self.attrs!r}, "
                f"encoding={self.encoding!r}, errors={self.errors!r}, "
                f"block_size={self.block_size!r}, max_requests={self.max_requests!r})")

    @staticmethod
    async def _touch_callback(host_info, global_info, command, cp, caller):
        """Static callback method for builtin creation (touch)"""

        # Validate host_info (matching Rmdir error handling)
        required_keys = ['host', 'username', 'password']
        for key in required_keys:
            if key not in host_info or host_info[key] is None:
                raise ValueError(f"Missing or invalid value for '{key}' in host_info.")

        # Validate caller attributes (matching Rmdir error handling)
        if caller.path is None:
            raise ValueError("The 'path' attribute of the caller cannot be None.")

        try:
            # Connect to the SSH server and create the builtin
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    # Open the builtin with specified parameters to create it
                    # File is immediately closed after creation, no data written
                    async with sftp.open(
                            path=caller.path,
                            pflags_or_mode=caller.pflags_or_mode,
                            attrs=caller.attrs,
                            encoding=caller.encoding,
                            errors=caller.errors,
                            block_size=caller.block_size,
                            max_requests=caller.max_requests
                    ) as file:
                        # File is created but we don't write anything to it
                        # The context manager will automatically close the builtin
                        pass
        except (OSError, asyncssh.Error) as exc:
            raise  # Re-raise the exception to handle it in the caller

    def execute(self):
        r = yield Command(f"{self}", local=True, callback=self._touch_callback, caller=self)
        r.executed = True
        r.changed = False
        return r
