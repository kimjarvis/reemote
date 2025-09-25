import asyncssh
from reemote.operation import Operation


class Chmod:
    """
    A class to encapsulate the functionality of chmod (change file mode) in
    Unix-like operating systems.

    Attributes:
        path (str): The file or directory path to change permissions for.
        mode (int): The new file permissions, expressed as an octal integer (e.g., 0o755).
        hosts (list): The list of hosts on which the permission change is to be performed.
        follow_symlinks (bool): Whether or not to follow symbolic links (default: True).

    **Examples:**

    .. code:: python

        yield Chmod(
            path='/home/user/script.sh',
            mode=0o755,
            hosts=["10.156.135.16", "10.156.135.17"]
        )

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution.

    Notes:
        If hosts is None or empty, the operation will execute on the current host.
    """

    def __init__(self, path: str, mode: int, hosts: list = None, follow_symlinks: bool = True):
        self.path = path
        self.mode = mode
        self.hosts = hosts
        self.follow_symlinks = follow_symlinks

    def __repr__(self):
        return f"Chmod(path={self.path!r}, mode={oct(self.mode)!r}, hosts={self.hosts!r}, follow_symlinks={self.follow_symlinks!r})"

    @staticmethod
    async def _chmod_callback(host_info, global_info, command, cp, caller):
        print(f"{caller}")
        """Static callback method for file permission change"""

        # Check if this host is in the target hosts list or if hosts list is empty/None
        if (caller.hosts is None or
                not caller.hosts or
                host_info["host"] in caller.hosts):

            async def run_client():
                try:
                    async with asyncssh.connect(**host_info) as conn:
                        async with conn.start_sftp_client() as sftp:
                            # Change the permissions of the remote file/directory
                            await sftp.chmod(
                                path=caller.path,
                                mode=caller.mode,
                                follow_symlinks=caller.follow_symlinks
                            )
                            # print(f"Changed permissions of {caller.path} to {oct(caller.mode)} on {host_info['host']}")
                except (OSError, asyncssh.Error) as exc:
                    print(f'chmod operation failed on {host_info["host"]}: {str(exc)}')
                    raise

            try:
                await run_client()
            except Exception as e:
                print(f"An error occurred on {host_info['host']}: {e}")
                return None

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._chmod_callback, caller=self)
        r.executed = True
        r.changed = True  # Set to True since chmod typically changes the system state