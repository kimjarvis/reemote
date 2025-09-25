import os
import asyncssh
from reemote.operation import Operation
from typing import Optional, Callable, Union


class Mget_files:
    """
    A class to encapsulate the functionality of multiple file downloads using SFTP.
    It allows users to download multiple remote files to local host with full parameter support.

    Attributes:
        remotepaths (str): The remote file or directory path(s) to download.
        localpath (str): The local path where files will be downloaded.
        hosts (list): The list of hosts from which files will be downloaded.
        preserve (bool): Preserve file attributes (permissions, timestamps).
        recurse (bool): Recursively download directories.
        follow_symlinks (bool): Follow symbolic links during download.
        sparse (bool): Create sparse files on the local system.
        block_size (int): Block size for file transfers.
        max_requests (int): Maximum number of concurrent transfer requests.
        progress_handler (Callable): Callback for transfer progress.
        error_handler (Callable): Callback for handling errors.

    **Examples:**

    .. code:: python

        remote_dir = '/home/user/remote_data'
        local_dir = '/home/user/local_downloads'

        r = yield Mget_files(
            remotepaths=f"{remote_dir}/*.log",
            localpath=local_dir,
            preserve=True,
            recurse=True,
            progress_handler=my_progress_callback
        )
        r = yield Shell(f"ls -la {local_dir}")
        print(r.cp.stdout)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        If hosts is None or empty, the operation will execute on the current host.
    """

    def __init__(self,
                 remotepaths: str,
                 localpath: Optional[str] = None,
                 hosts: list = None,
                 preserve: bool = False,
                 recurse: bool = False,
                 follow_symlinks: bool = False,
                 sparse: bool = True,
                 block_size: int = -1,
                 max_requests: int = -1,
                 progress_handler: Optional[Callable] = None,
                 error_handler: Optional[Callable] = None):
        self.remotepaths = remotepaths
        self.localpath = localpath
        self.hosts = hosts
        self.preserve = preserve
        self.recurse = recurse
        self.follow_symlinks = follow_symlinks
        self.sparse = sparse
        self.block_size = block_size
        self.max_requests = max_requests
        self.progress_handler = progress_handler
        self.error_handler = error_handler

    def __repr__(self):
        return (f"Mget_files(remotepaths={self.remotepaths!r}, "
                f"localpath={self.localpath!r}, "
                f"hosts={self.hosts!r}, "
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
    async def _mget_files_callback(host_info, sudo_global, command, cp, caller):
        print(f"{caller}")
        """Static callback method for multiple file download with full parameter support"""
        # Check if this host is in the target hosts list or if hosts list is empty/None
        if (caller.hosts is None or
                not caller.hosts or
                host_info["host"] in caller.hosts):

            print(f"Downloading files from host {host_info['host']}")

            async def run_client() -> None:
                try:
                    # Connect to the SSH server
                    async with asyncssh.connect(**host_info) as conn:
                        # Start an SFTP session
                        async with conn.start_sftp_client() as sftp:
                            await sftp.mget(
                                remotepaths=caller.remotepaths,
                                localpath=caller.localpath,
                                preserve=caller.preserve,
                                recurse=caller.recurse,
                                follow_symlinks=caller.follow_symlinks,
                                sparse=caller.sparse,
                                block_size=caller.block_size,
                                max_requests=caller.max_requests,
                                progress_handler=caller.progress_handler,
                                error_handler=caller.error_handler
                            )
                            print(f"Successfully downloaded files from {host_info['host']}")
                except (OSError, asyncssh.Error) as exc:
                    print(f'SFTP operation failed on {host_info["host"]}: {str(exc)}')
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
        r = yield Operation(f"{self}", local=True, callback=self._mget_files_callback, caller=self)
        r.executed = True
        r.changed = True