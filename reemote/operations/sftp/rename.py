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
        hosts (list): The list of hosts on which the rename operation is to be performed.

    **Examples:**

    .. code:: python

        yield Rename(
            oldpath='/home/user/oldname.txt',
            newpath='/home/user/newname.txt',
            hosts=["10.156.135.16", "10.156.135.17"]
        )

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution.

    Notes:
        If hosts is None or empty, the operation will execute on the current host.
        The flags parameter is only supported in SFTP version 5 and later.
        For older SFTP versions, only basic rename functionality is available.
    """

    def __init__(self, oldpath: str, newpath: str, flags: int = 0, hosts: list = None):
        self.oldpath = oldpath
        self.newpath = newpath
        self.flags = flags
        self.hosts = hosts

    def __repr__(self):
        return f"Rename(oldpath={self.oldpath!r}, newpath={self.newpath!r}, flags={self.flags!r}, hosts={self.hosts!r})"

    @staticmethod
    async def _rename_callback(host_info, global_info, command, cp, caller):
        print(f"{caller}")
        """Static callback method for file/directory rename"""

        # Check if this host is in the target hosts list or if hosts list is empty/None
        if (caller.hosts is None or
                not caller.hosts or
                host_info["host"] in caller.hosts):

            async def run_client():
                try:
                    async with asyncssh.connect(**host_info) as conn:
                        async with conn.start_sftp_client() as sftp:
                            # Rename the remote file/directory
                            await sftp.rename(caller.oldpath, caller.newpath, caller.flags)
                except (OSError, asyncssh.Error) as exc:
                    print(f'SFTP rename operation failed on {host_info["host"]}: {str(exc)}')
                    raise

            try:
                await run_client()
            except Exception as e:
                print(f"An error occurred on {host_info['host']}: {e}")
                return None

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._rename_callback, caller=self)
        r.executed = True
        r.changed = False