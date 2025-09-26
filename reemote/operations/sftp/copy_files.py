import asyncssh
from reemote.operation import Operation
from typing import Optional, Callable, Union


class Copy_files:
    """
    A class to encapsulate the functionality of remote-to-remote file copying using SFTP.
    It allows users to copy multiple remote files to new remote locations with full parameter support.

    Attributes:
        srcpaths (str): The remote source file or directory path(s) to copy.
        dstpath (str): The remote destination path where files will be copied.
        preserve (bool): Preserve file attributes (permissions, timestamps).
        recurse (bool): Recursively copy directories.
        follow_symlinks (bool): Follow symbolic links during copy.
        sparse (bool): Create sparse files on the remote system.
        block_size (int): Block size for file transfers.
        max_requests (int): Maximum number of concurrent transfer requests.
        progress_handler (Callable): Callback for copy progress.
        error_handler (Callable): Callback for handling errors.
        remote_only (bool): Whether to only allow remote copy operations.

    **Examples:**

    .. code:: python

        src_dir = '/home/user/'
        dst_dir = '/home/user/'
        r = yield Copy_files(
            srcpaths=src_dir + '/example.txt',
            dstpath=dst_dir+ '/example1.txt',
            preserve=True,
            recurse=True,
            progress_handler=my_progress_callback,
        )

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.
    """

    def __init__(self,
                 srcpaths: str,
                 dstpath: str,
                 preserve: bool = False,
                 recurse: bool = False,
                 follow_symlinks: bool = False,
                 sparse: bool = True,
                 block_size: int = -1,
                 max_requests: int = -1,
                 progress_handler: Optional[Callable] = None,
                 error_handler: Optional[Callable] = None,
                 remote_only: bool = True):
        self.srcpaths = srcpaths
        self.dstpath = dstpath
        self.preserve = preserve
        self.recurse = recurse
        self.follow_symlinks = follow_symlinks
        self.sparse = sparse
        self.block_size = block_size
        self.max_requests = max_requests
        self.progress_handler = progress_handler
        self.error_handler = error_handler
        self.remote_only = remote_only

    def __repr__(self):
        return (f"Copy_files(srcpaths={self.srcpaths!r}, "
                f"dstpath={self.dstpath!r}, "
                f"preserve={self.preserve!r}, "
                f"recurse={self.recurse!r}, "
                f"follow_symlinks={self.follow_symlinks!r}, "
                f"sparse={self.sparse!r}, "
                f"block_size={self.block_size!r}, "
                f"max_requests={self.max_requests!r}, "
                f"remote_only={self.remote_only!r})")

    @staticmethod
    async def _copy_callback(host_info, global_info, command, cp, caller):
        """Static callback method for remote-to-remote file copying with full parameter support"""
        
        # Validate host_info
        required_keys = ['host', 'username', 'password']
        for key in required_keys:
            if key not in host_info or host_info[key] is None:
                raise ValueError(f"Missing or invalid value for '{key}' in host_info.")

        # Validate caller attributes
        if caller.srcpaths is None:
            raise ValueError("The 'srcpaths' attribute of the caller cannot be None.")
        if caller.dstpath is None:
            raise ValueError("The 'dstpath' attribute of the caller cannot be None.")

        try:
            # Connect to the SSH server
            async with asyncssh.connect(**host_info) as conn:
                # Start an SFTP session
                async with conn.start_sftp_client() as sftp:
                    # Use the copy method for remote-to-remote copying
                    await sftp.copy(
                        srcpaths=caller.srcpaths,
                        dstpath=caller.dstpath,
                        preserve=caller.preserve,
                        recurse=caller.recurse,
                        follow_symlinks=caller.follow_symlinks,
                        sparse=caller.sparse,
                        block_size=caller.block_size,
                        max_requests=caller.max_requests,
                        progress_handler=caller.progress_handler,
                        error_handler=caller.error_handler,
                        remote_only=caller.remote_only
                    )
                    return f"Successfully copied files on {host_info['host']}"
        except (OSError, asyncssh.Error) as exc:
            raise  # Re-raise the exception to handle it in the caller

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._copy_callback, caller=self)
        r.executed = True
        r.changed = True
        return r