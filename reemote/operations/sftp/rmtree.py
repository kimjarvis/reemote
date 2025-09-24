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
        hosts (list): The list of hosts on which the directory tree is to be removed.

    **Examples:**

    .. code:: python

        yield Rmtree(path='/home/user/hfs',
           ignore_errors=False,
           hosts=["10.156.135.16", "10.156.135.17"],
        )

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        If hosts is None or empty, the operation will execute on the current host.
    """

    def __init__(self,
                 path: str,
                 ignore_errors: bool = False,
                 hosts: list = None):
        self.path = path
        self.ignore_errors = ignore_errors
        self.hosts = hosts

    def __repr__(self):
        return (f"Rmtree(path={self.path!r}, "
                f"ignore_errors={self.ignore_errors!r}, "
                f"hosts={self.hosts!r})")

    @staticmethod
    async def _rmtree_callback(host_info, global_info, command, cp, caller):
        print(f"{caller}")
        """Static callback method for recursive directory removal"""

        # Check if this host is in the target hosts list or if hosts list is empty/None
        if (caller.hosts is None or
                not caller.hosts or
                host_info["host"] in caller.hosts):

            async def run_client():
                try:
                    async with asyncssh.connect(**host_info) as conn:
                        async with conn.start_sftp_client() as sftp:
                            # Use the rmtree method provided by asyncssh
                            await sftp.rmtree(path=caller.path,
                                              ignore_errors=caller.ignore_errors)
                except (OSError, asyncssh.Error) as exc:
                    print(f'SFTP operation failed on {host_info["host"]}: {str(exc)}')
                    raise  # Re-raise the exception to handle it in the caller

            try:
                await run_client()
            except Exception as e:
                print(f"An error occurred on {host_info['host']}: {e}")
                if not caller.ignore_errors:
                    raise  # Only re-raise if we're not ignoring errors
                return None  # Return None if ignoring errors

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._rmtree_callback, caller=self)
        r.executed = True
        r.changed = False