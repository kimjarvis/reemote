import asyncssh
from reemote.operation import Operation
from typing import Union, List, Optional
import os


class Download:
    """
    A class to handle secure file downloads from remote hosts using SCP (Secure Copy Protocol).

    This class provides functionality to download files or directories from one or more
    remote hosts to a local destination path. It supports various SCP options including
    preserving file attributes and recursive directory downloads.

    Attributes:
        srcpaths (Union[str, List[str]]): Source file or directory path(s) on remote host(s).
            Can be a single string path or a list of paths. Supports host:path format.
        dstpath (str): Local destination path where files will be downloaded.
        hosts (List[str], optional): List of target host identifiers. If None or empty,
            operation will attempt to run on all available hosts.
        preserve (bool): If True, preserves file modification times, access times,
            and modes from the original files. Defaults to False.
        recurse (bool): If True, recursively copies entire directories. Defaults to False.
        block_size (int): Block size used for file transfers in bytes. Defaults to 16384.
        port (int): SSH port to use for connections. Defaults to 22.

    **Examples:**

    .. code:: python

        yield Download(
            srcpaths='/home/user/*.txt',  # Remove the host: prefix
            dstpath='/home/kim/',
            hosts=["10.156.135.16"],
            recurse=True
        )

    Note:
        This class requires proper SSH credentials to be configured for the target hosts.
        The actual download operation is executed asynchronously through the Operation class.
    """

    def __init__(self,
                 srcpaths: Union[str, List[str]],
                 dstpath: str,
                 hosts: List[str] = None,
                 preserve: bool = False,
                 recurse: bool = False,
                 block_size: int = 16384,
                 port: int = 22):
        self.srcpaths = srcpaths
        self.dstpath = dstpath
        self.hosts = hosts
        self.preserve = preserve
        self.recurse = recurse
        self.block_size = block_size
        self.port = port

    def __repr__(self):
        return f"Download(srcpaths={self.srcpaths!r}, dstpath={self.dstpath!r}, hosts={self.hosts!r}, preserve={self.preserve}, recurse={self.recurse})"

    @staticmethod
    async def _download_callback(host_info, global_info, command, cp, caller):
        """Static callback method for file download"""

        # Check if this host is in the target hosts list or if hosts list is empty/None
        if (caller.hosts is None or
                not caller.hosts or
                host_info["host"] in caller.hosts):

            async def run_client():
                try:
                    # Create proper connection parameters
                    connect_kwargs = {
                        'host': host_info['host'],
                        'username': host_info.get('username'),
                        'password': host_info.get('password'),
                        'client_keys': host_info.get('client_keys'),
                        'known_hosts': host_info.get('known_hosts')
                    }

                    # Remove None values
                    connect_kwargs = {k: v for k, v in connect_kwargs.items() if v is not None}

                    # Set port if specified and different from default
                    if caller.port != 22:
                        connect_kwargs['port'] = caller.port

                    print(f"Connecting to {host_info['host']} with username: {host_info.get('username')}")

                    async with asyncssh.connect(**connect_kwargs) as conn:
                        print(f"Connected successfully to {host_info['host']}")

                        # Handle source paths - remove any host prefix since we're already connected
                        if isinstance(caller.srcpaths, list):
                            srcpaths = []
                            for srcpath in caller.srcpaths:
                                # Remove host: prefix if present
                                if ':' in srcpath:
                                    # Extract path after host: part
                                    srcpath = srcpath.split(':', 1)[1]
                                srcpaths.append(srcpath)
                        else:
                            if ':' in caller.srcpaths:
                                # Extract path after host: part
                                srcpaths = caller.srcpaths.split(':', 1)[1]
                            else:
                                srcpaths = caller.srcpaths

                        print(f"Downloading: {srcpaths} -> {caller.dstpath}")

                        await asyncssh.scp(
                            (conn, srcpaths),  # Use connection object instead of host string
                            caller.dstpath,
                            preserve=caller.preserve,
                            recurse=caller.recurse,
                            block_size=caller.block_size
                        )
                        print(f"Download completed from {host_info['host']}")

                except (OSError, asyncssh.Error) as exc:
                    print(f'SCP download failed on {host_info["host"]}: {str(exc)}')
                    raise

            try:
                await run_client()
            except Exception as e:
                print(f"An error occurred on {host_info['host']}: {e}")
                return None

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._download_callback, caller=self)
        r.executed = True
        r.changed = True