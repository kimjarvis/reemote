import asyncssh
from reemote.operation import Operation
from typing import Optional


class Get_lstat:
    """
    A class to encapsulate the functionality of lstat for getting file attributes
    using SFTP lstat in Unix-like operating systems. Unlike stat, lstat returns
    attributes of symlinks themselves rather than their targets.

    Attributes:
        hosts (list): The list of hosts from which to get file attributes.
        file_path (str): The path of the file to get attributes for.
        flags (int): Flags indicating attributes of interest (SFTPv4 or later)

    **Examples:**

    .. code:: python

        yield Get_lstat(
            hosts=["10.156.135.16", "10.156.135.17"],
            file_path="/path/to/symlink.txt"
        )

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution. The file attributes for each
        host will be returned in the operation result.

    Notes:
        If hosts is None or empty, the operation will execute on the current host.
        Unlike stat, lstat returns attributes of symlinks themselves rather than
        the files they point to.
    """

    def __init__(self, path: str, hosts: list = None):
        self.path = path
        self.hosts = hosts

    def __repr__(self):
        return f"Get_lstat(file_path={self.path!r}, hosts={self.hosts!r})"

    @staticmethod
    def _sftp_attrs_to_dict(attrs):
        """Convert SFTPAttrs object to a JSON-serializable dictionary"""
        if attrs is None:
            return None

        result = {}
        # Add all available attributes
        if hasattr(attrs, 'size') and attrs.size is not None:
            result['size'] = attrs.size
        if hasattr(attrs, 'permissions') and attrs.permissions is not None:
            result['permissions'] = oct(attrs.permissions)  # Convert to octal string
        if hasattr(attrs, 'uid') and attrs.uid is not None:
            result['uid'] = attrs.uid
        if hasattr(attrs, 'gid') and attrs.gid is not None:
            result['gid'] = attrs.gid
        if hasattr(attrs, 'atime') and attrs.atime is not None:
            result['atime'] = attrs.atime
        if hasattr(attrs, 'mtime') and attrs.mtime is not None:
            result['mtime'] = attrs.mtime
        if hasattr(attrs, 'nlink') and attrs.nlink is not None:
            result['nlink'] = attrs.nlink
        if hasattr(attrs, 'type') and attrs.type is not None:
            result['type'] = str(attrs.type)
        if hasattr(attrs, 'extended') and attrs.extended is not None:
            result['extended'] = dict(attrs.extended) if attrs.extended else {}

        return result

    @staticmethod
    async def _get_lstat_callback(host_info, sudo_global, command, cp, caller):
        """Static callback method for getting file status using lstat"""
        if (caller.hosts is None or
                not caller.hosts or
                host_info["host"] in caller.hosts):

            print(f"Getting file status using lstat on host {host_info['host']}")

            async def run_client() -> None:
                try:
                    async with asyncssh.connect(**host_info) as conn:
                        async with conn.start_sftp_client() as sftp:
                            # Use lstat instead of stat to get symlink attributes
                            lstat_result = await sftp.lstat(caller.path)
                            # Convert SFTPAttrs to dictionary for JSON serialization
                            return caller._sftp_attrs_to_dict(lstat_result)
                except (OSError, asyncssh.Error) as exc:
                    print(f'SFTP lstat operation failed on {host_info["host"]}: {str(exc)}')
                    raise

            try:
                result = await run_client()
                return result
            except KeyboardInterrupt:
                print('Operation interrupted by user.')
                raise
            except Exception as e:
                print(f"An error occurred on {host_info['host']}: {e}")
                return None

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._get_lstat_callback, caller=self)
        r.executed = True
        r.changed = False
        return r