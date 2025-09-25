import asyncssh
from reemote.operation import Operation


class Chown:
    """
    A class to encapsulate the functionality of chown (change owner) in
    Unix-like operating systems.

    Attributes:
        path (str): The file path to change ownership of.
        uid (int, optional): The new user id to assign to the file.
        gid (int, optional): The new group id to assign to the file.
        owner (str, optional): The new owner to assign to the file (SFTPv4 only).
        group (str, optional): The new group to assign to the file (SFTPv4 only).
        hosts (list): The list of hosts on which the ownership change is to be performed.

    **Examples:**

    .. code:: python

        yield Chown(
            path='/home/user/file.txt',
            owner='newuser',
            group='newgroup',
            hosts=["10.156.135.16", "10.156.135.17"]
        )

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution.

    Notes:
        If hosts is None or empty, the operation will execute on the current host.
    """

    def __init__(self, path: str, uid: int = None, gid: int = None,
                 owner: str = None, group: str = None, hosts: list = None):
        self.path = path
        self.uid = uid
        self.gid = gid
        self.owner = owner
        self.group = group
        self.hosts = hosts

    def __repr__(self):
        return f"Chown(path={self.path!r}, uid={self.uid!r}, gid={self.gid!r}, owner={self.owner!r}, group={self.group!r}, hosts={self.hosts!r})"

    @staticmethod
    async def _chown_callback(host_info, global_info, command, cp, caller):
        print(f"{caller}")
        """Static callback method for file ownership change"""

        # Check if this host is in the target hosts list or if hosts list is empty/None
        if (caller.hosts is None or
                not caller.hosts or
                host_info["host"] in caller.hosts):

            async def run_client():
                try:
                    async with asyncssh.connect(**host_info) as conn:
                        async with conn.start_sftp_client() as sftp:
                            # Change the ownership of the remote file
                            await sftp.setstat(
                                caller.path,
                                asyncssh.SFTPAttrs(
                                    uid=caller.uid,
                                    gid=caller.gid,
                                    owner=caller.owner,
                                    group=caller.group
                                )
                            )
                except (OSError, asyncssh.Error) as exc:
                    print(f'SFTP operation failed on {host_info["host"]}: {str(exc)}')
                    raise

            try:
                await run_client()
            except Exception as e:
                print(f"An error occurred on {host_info['host']}: {e}")
                return None

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._chown_callback, caller=self)
        r.executed = True
        r.changed = False