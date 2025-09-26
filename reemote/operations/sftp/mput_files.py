import os
import asyncssh
from reemote.operation import Operation
from typing import Optional, Callable, Union


class Mput_files:
    """
    A class to encapsulate the functionality of multiple file uploads using SFTP.
    It allows users to upload multiple local files to remote hosts with full parameter support.

    Attributes:
        localpaths (str): The local file or directory path(s) to upload.
        remotepath (str): The remote path where files will be uploaded.
        preserve (bool): Preserve file attributes (permissions, timestamps).
        recurse (bool): Recursively upload directories.
        follow_symlinks (bool): Follow symbolic links during upload.
        sparse (bool): Create sparse files on the remote system.
        block_size (int): Block size for file transfers.
        max_requests (int): Maximum number of concurrent transfer requests.
        progress_handler (Callable): Callback for transfer progress.
        error_handler (Callable): Callback for handling errors.

    **Examples:**

    .. code:: python

        from reemote.operations.filesystem.mput_files import Mput_files
        dir='/home/user/dir'
        r = yield Mkdir(path=dir, attrs=SFTPAttrs(permissions=0o755))
        r = yield Mput_files(
            localpaths='~/reemote/development/hfs/*',
            remotepath=dir,
            preserve=True,
            recurse=True,
            progress_handler=my_progress_callback
        )
        r = yield Shell(f"tree {dir}")
        print(r.cp.stdout)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.
    """

    def __init__(self,
                 localpaths: str,
                 remotepath: str,
                 preserve: bool = False,
                 recurse: bool = False,
                 follow_symlinks: bool = False,
                 sparse: bool = True,
                 block_size: int = -1,
                 max_requests: int = -1,
                 progress_handler: Optional[Callable] = None,
                 error_handler: Optional[Callable] = None):
        self.localpaths = localpaths
        self.remotepath = remotepath
        self.preserve = preserve
        self.recurse = recurse
        self.follow_symlinks = follow_symlinks
        self.sparse = sparse
        self.block_size = block_size
        self.max_requests = max_requests
        self.progress_handler = progress_handler
        self.error_handler = error_handler

    def __repr__(self):
        return (f"Mput_files(localpaths={self.localpaths!r}, "
                f"remotepath={self.remotepath!r}, "
                f"preserve={self.preserve!r}, "
                f"recurse={self.recurse!r}, "
                f"follow_symlinks={self.follow_symlinks!r}, "
                f"sparse={self.sparse!r}, "
                f"block_size={self.block_size!r}, "
                f"max_requests={self.max_requests!r})")

    @staticmethod
    def get_absolute_path(path):
        """
        Expands a given path to its absolute form, resolving '~' to the user's home directory,
        while preserving any wildcard (*) in the path.

        Args:
            path (str): The input file path, which may include '~' and/or wildcards.

        Returns:
            str: The absolute path with wildcards preserved.
        """
        # Step 1: Expand ~ to the user's home directory
        expanded_path = os.path.expanduser(path)

        # Step 2: Resolve the absolute path (while keeping the wildcard)
        absolute_path_with_glob = os.path.abspath(expanded_path)

        return absolute_path_with_glob

    @staticmethod
    async def _mput_files_callback(host_info, global_info, command, cp, caller):
        """Static callback method for multiple file upload with full parameter support"""

        # Validate host_info (matching Mget_files error handling)
        required_keys = ['host', 'username', 'password']
        for key in required_keys:
            if key not in host_info or host_info[key] is None:
                raise ValueError(f"Missing or invalid value for '{key}' in host_info.")

        # Validate caller attributes (matching Mget_files error handling)
        if caller.localpaths is None:
            raise ValueError("The 'localpaths' attribute of the caller cannot be None.")
        if caller.remotepath is None:
            raise ValueError("The 'remotepath' attribute of the caller cannot be None.")

        try:
            # Connect to the SSH server
            async with asyncssh.connect(**host_info) as conn:
                # Start an SFTP session
                async with conn.start_sftp_client() as sftp:
                    # Use the mput method for file upload
                    await sftp.mput(
                        localpaths=Mput_files.get_absolute_path(caller.localpaths),
                        remotepath=caller.remotepath,
                        preserve=caller.preserve,
                        recurse=caller.recurse,
                        follow_symlinks=caller.follow_symlinks,
                        sparse=caller.sparse,
                        block_size=caller.block_size,
                        max_requests=caller.max_requests,
                        progress_handler=caller.progress_handler,
                        error_handler=caller.error_handler
                    )
                    return f"Successfully uploaded files to {host_info['host']}"
        except (OSError, asyncssh.Error) as exc:
            raise  # Re-raise the exception to handle it in the caller

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._mput_files_callback, caller=self)
        r.executed = True
        r.changed = True
        return r