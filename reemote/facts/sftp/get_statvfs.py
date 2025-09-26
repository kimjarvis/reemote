import asyncssh
from reemote.operation import Operation


class Get_statvfs:
    """
    A class to encapsulate the functionality of getting filesystem statistics
    using SFTP statvfs (stat on a filesystem path) in Unix-like operating systems.

    Attributes:
        path (str): The filesystem path to get statistics for.

    **Examples:**
    .. code:: python

        yield Get_statvfs(path="/home/user")

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution. The filesystem statistics for each
        host will be returned in the operation result.

    Notes:
        The operation will execute on all hosts in the current execution context.
        The path must be a valid filesystem path accessible via SFTP.
    """

    def __init__(self, path: str = None):
        self.path = path

    def __repr__(self):
        return f"Get_statvfs(path={self.path!r})"

    @staticmethod
    def _sftp_vfs_attrs_to_dict(attrs):
        """Convert SFTPVFSAttrs object to a JSON-serializable dictionary"""
        if attrs is None:
            return None

        result = {}
        # Add all available VFS attributes
        if hasattr(attrs, 'f_bsize') and attrs.f_bsize is not None:
            result['f_bsize'] = attrs.f_bsize  # File system block size
        if hasattr(attrs, 'f_frsize') and attrs.f_frsize is not None:
            result['f_frsize'] = attrs.f_frsize  # Fundamental file system block size
        if hasattr(attrs, 'f_blocks') and attrs.f_blocks is not None:
            result['f_blocks'] = attrs.f_blocks  # Total data blocks in file system
        if hasattr(attrs, 'f_bfree') and attrs.f_bfree is not None:
            result['f_bfree'] = attrs.f_bfree  # Free blocks in file system
        if hasattr(attrs, 'f_bavail') and attrs.f_bavail is not None:
            result['f_bavail'] = attrs.f_bavail  # Free blocks available to unprivileged user
        if hasattr(attrs, 'f_files') and attrs.f_files is not None:
            result['f_files'] = attrs.f_files  # Total file nodes in file system
        if hasattr(attrs, 'f_ffree') and attrs.f_ffree is not None:
            result['f_ffree'] = attrs.f_ffree  # Free file nodes in file system
        if hasattr(attrs, 'f_favail') and attrs.f_favail is not None:
            result['f_favail'] = attrs.f_favail  # Free file nodes available to unprivileged user
        if hasattr(attrs, 'f_fsid') and attrs.f_fsid is not None:
            result['f_fsid'] = attrs.f_fsid  # File system ID
        if hasattr(attrs, 'f_flag') and attrs.f_flag is not None:
            result['f_flag'] = attrs.f_flag  # Mount flags
        if hasattr(attrs, 'f_namemax') and attrs.f_namemax is not None:
            result['f_namemax'] = attrs.f_namemax  # Maximum filename length

        return result

    @staticmethod
    async def _getstatvfs_callback(host_info, global_info, command, cp, caller):
        """Static callback method for getting filesystem statistics from a path"""
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                # Get filesystem statistics using statvfs on the path
                if caller.path:
                    # Convert path to bytes as required by asyncssh statvfs
                    path_bytes = caller.path.encode('utf-8')
                    vfs_attrs = await sftp.statvfs(path_bytes)
                    # Convert to JSON-serializable dictionary
                    return caller._sftp_vfs_attrs_to_dict(vfs_attrs)
                else:
                    raise ValueError("Path must be provided for statvfs operation")

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._getstatvfs_callback, caller=self)
        r.executed = True
        r.changed = False
        return r