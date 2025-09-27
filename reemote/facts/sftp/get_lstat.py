import asyncssh
from reemote.operation import Operation
from typing import Optional


class Get_lstat:
    """
    A class to encapsulate the functionality of lstat for getting file attributes
    using SFTP lstat in Unix-like operating systems. Unlike stat, lstat returns
    attributes of symlinks themselves rather than their targets.

    Attributes:
        file_path (str): The path of the file to get attributes for.

    **Examples:**

    .. code:: python

        yield Get_lstat(file_path="/path/to/symlink.txt")

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution. The file attributes for each
        host will be returned in the operation result.

    Notes:
        The operation will execute on all hosts in the current execution context.
        Unlike stat, lstat returns attributes of symlinks themselves rather than
        the files they point to.
    """

    def __init__(self, path: str):
        self.path = path

    def __repr__(self):
        return f"Get_lstat(file_path={self.path!r})"

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
    async def _get_lstat_callback(host_info, global_info, command, cp, caller):
        """Static callback method for getting file status using lstat"""
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                # Check if the path is provided
                if caller.path:
                    # Use lstat instead of stat to get symlink attributes
                    lstat_result = await sftp.lstat(caller.path)
                    # Convert SFTPAttrs to dictionary for JSON serialization
                    return caller._sftp_attrs_to_dict(lstat_result)
                else:
                    raise ValueError("Path must be provided for lstat operation")

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._get_lstat_callback, caller=self)
        r.executed = True
        r.changed = False
        return r