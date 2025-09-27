import asyncssh
import asyncio
import sys
import posixpath
from reemote.operation import Operation
from typing import cast


class Rmtree:
    """
    A class to encapsulate the functionality of rmtree (recursively remove directory tree).

    Attributes:
        path (str): The directory path to remove recursively.
        ignore_errors (bool): Whether to ignore errors during removal.

    **Examples:**

    .. code:: python

        yield Rmtree(path='/home/user/hfs', ignore_errors=False)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.
    """

    def __init__(self,
                 path: str,
                 ignore_errors: bool = False):
        self.path = path
        self.ignore_errors = ignore_errors

    def __repr__(self):
        return (f"Rmtree(path={self.path!r}, "
                f"ignore_errors={self.ignore_errors!r})")

    @staticmethod
    async def _rmtree_callback(host_info, global_info, command, cp, caller):
        """Static callback method for recursive directory removal"""

        # Validate host_info (matching Rmdir error handling)
        required_keys = ['host', 'username', 'password']
        for key in required_keys:
            if key not in host_info or host_info[key] is None:
                raise ValueError(f"Missing or invalid value for '{key}' in host_info.")

        # Validate caller attributes (matching Rmdir error handling)
        if caller.path is None:
            raise ValueError("The 'path' attribute of the caller cannot be None.")

        try:
            # Connect to the SSH server and recursively remove the directory
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    # Use the rmtree method provided by asyncssh
                    await sftp.rmtree(path=caller.path,
                                      ignore_errors=caller.ignore_errors)
        except (OSError, asyncssh.Error) as exc:
            raise  # Re-raise the exception to handle it in the caller

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._rmtree_callback, caller=self)
        r.executed = True
        r.changed = False
        return r