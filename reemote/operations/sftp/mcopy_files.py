import os
import asyncssh
from reemote.operation import Operation
from typing import Optional, Callable, Union


class Mcopy_files:
    """
    A class to encapsulate the functionality of remote-to-remote file copying using SFTP
    with glob pattern matching (mcopy functionality).

    Attributes:
        srcpaths (str): The remote source file or directory path(s) to copy with wildcard patterns.
        dstpath (str): The remote destination path where files will be copied.
        hosts (list): The list of hosts on which the copy operation will be performed.
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
        dst_dir = '/home/user/backup/'
        r = yield Mkdir(path=dst_dir, attrs=SFTPAttrs(permissions=0o755), hosts=["10.156.135.16"])
        r = yield Mcopy_files(
            srcpaths=src_dir + '*.txt',  # Using wildcard pattern
            dstpath=dst_dir,
            preserve=True,
            recurse=True,
            progress_handler=my_progress_callback,
            hosts=["10.156.135.16"]
        )
        r = yield Shell(f"ls {dst_dir}/backup/")
        print(r.cp.stdout)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        If hosts is None or empty, the operation will execute on the current host.
        This implementation uses mcopy which supports wildcard patterns in source paths.
    """

    def __init__(self,
                 srcpaths: str,
                 dstpath: str,
                 hosts: list = None,
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
        self.hosts = hosts
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
        return (f"Mcopy_files(srcpaths={self.srcpaths!r}, "
                f"dstpath={self.dstpath!r}, "
                f"hosts={self.hosts!r}, "
                f"preserve={self.preserve!r}, "
                f"recurse={self.recurse!r}, "
                f"follow_symlinks={self.follow_symlinks!r}, "
                f"sparse={self.sparse!r}, "
                f"block_size={self.block_size!r}, "
                f"max_requests={self.max_requests!r}, "
                f"remote_only={self.remote_only!r})")

    @staticmethod
    async def _mcopy_files_callback(host_info, sudo_global, command, cp, caller):
        print(f"{caller}")
        """Static callback method for remote-to-remote file copying with glob pattern support"""
        # Check if this host is in the target hosts list or if hosts list is empty/None
        if (caller.hosts is None or
                not caller.hosts or
                host_info["host"] in caller.hosts):

            print(f"Copying files with glob patterns on host {host_info['host']}")
            print(f"Source patterns: {caller.srcpaths}")
            print(f"Destination: {caller.dstpath}")

            async def run_client() -> None:
                try:
                    # Connect to the SSH server
                    async with asyncssh.connect(**host_info) as conn:
                        # Start an SFTP session
                        print(f"Connecting to {host_info['host']}")
                        async with conn.start_sftp_client() as sftp:
                            print(f"Using mcopy with SFTP client")
                            # Use the mcopy method for glob pattern matching
                            await sftp.mcopy(
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
                            print(f"Successfully copied files using glob patterns on {host_info['host']}")
                except (OSError, asyncssh.Error) as exc:
                    print(f'mcopy operation failed on {host_info["host"]}: {str(exc)}')
                    raise

            try:
                # Run the client coroutine
                await run_client()
            except KeyboardInterrupt:
                print('Operation interrupted by user.')
                raise
            except Exception as e:
                print(f"An error occurred on {host_info['host']}: {e}")
                return None

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._mcopy_files_callback, caller=self)
        r.executed = True
        r.changed = True