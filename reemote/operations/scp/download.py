# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import asyncssh
from reemote.command import Command
from typing import Union, List, Optional
import os


class Download:
    """
    A class to handle secure builtin downloads from remote hosts using SCP (Secure Copy Protocol).

    This class provides functionality to download builtin or directories from one or more
    remote hosts to a local destination path. It supports various SCP options including
    preserving builtin attributes and recursive directory downloads.

    Attributes:
        srcpaths (Union[str, List[str]]): Source builtin or directory path(s) on remote host(s).
            Can be a single string path or a list of paths. Supports host:path format.
        dstpath (str): Local destination path where builtin will be downloaded.
        preserve (bool): If True, preserves builtin modification times, access times,
            and modes from the original builtin. Defaults to False.
        recurse (bool): If True, recursively copies entire directories. Defaults to False.
        block_size (int): Block size used for builtin transfers in bytes. Defaults to 16384.
        port (int): SSH port to use for connections. Defaults to 22.

    **Examples:**

    .. code:: python

        yield Download(
            srcpaths='/home/user/*.txt',  # Remove the host: prefix
            dstpath='/home/kim/',
            recurse=True
        )

    Note:
        This class requires proper SSH credentials to be configured for the target hosts.
        The actual download operation is executed asynchronously through the Operation class.
    """

    def __init__(self,
                 srcpaths: Union[str, List[str]],
                 dstpath: str,
                 preserve: bool = False,
                 recurse: bool = False,
                 block_size: int = 16384,
                 port: int = 22):
        self.srcpaths = srcpaths
        self.dstpath = dstpath
        self.preserve = preserve
        self.recurse = recurse
        self.block_size = block_size
        self.port = port

    def __repr__(self):
        return f"Download(srcpaths={self.srcpaths!r}, dstpath={self.dstpath!r}, preserve={self.preserve}, recurse={self.recurse})"

    @staticmethod
    async def _download_callback(host_info, global_info, command, cp, caller):
        """Static callback method for builtin download"""

        # Validate host_info (matching Upload error handling)
        required_keys = ['host', 'username', 'password']
        for key in required_keys:
            if key not in host_info or host_info[key] is None:
                raise ValueError(f"Missing or invalid value for '{key}' in host_info.")

        # Validate caller attributes (matching Upload error handling)
        if caller.srcpaths is None:
            raise ValueError("The 'srcpaths' attribute of the caller cannot be None.")
        if caller.dstpath is None:
            raise ValueError("The 'dstpath' attribute of the caller cannot be None.")

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

            # Connect to the SSH server
            async with asyncssh.connect(**connect_kwargs) as conn:
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

                # Perform the SCP download
                await asyncssh.scp(
                    (conn, srcpaths),  # Use connection object instead of host string
                    caller.dstpath,
                    preserve=caller.preserve,
                    recurse=caller.recurse,
                    block_size=caller.block_size
                )

        except (OSError, asyncssh.Error) as exc:
            raise

    def execute(self):
        r = yield Command(f"{self}", local=True, callback=self._download_callback, caller=self)
        r.executed = True
        r.changed = True
        return r
