import asyncssh
from reemote.operation import Operation
from typing import Union, List, Optional
import tempfile
import os


class Copy:
    """
    A class to handle secure file copying between remote hosts using SCP (Secure Copy Protocol).

    This class provides functionality to copy files or directories between remote hosts,
    from source hosts to destination hosts. It supports various SCP options including
    preserving file attributes and recursive directory copying.

    Attributes:
        srcpaths (Union[str, List[str]]): Source file or directory path(s). Can be a single
            string path or a list of paths. Supports host:path format for remote sources.
        dstpath (str): Destination path where files will be copied. Supports host:path format
            for remote destinations.
        src_hosts (List[str], optional): List of source host identifiers. If None or empty,
            operation will attempt to use all available hosts as sources.
        dst_hosts (List[str], optional): List of destination host identifiers. If None or empty,
            operation will attempt to use all available hosts as destinations.
        preserve (bool): If True, preserves file modification times, access times,
            and modes from the original files. Defaults to False.
        recurse (bool): If True, recursively copies entire directories. Defaults to False.
        block_size (int): Block size used for file transfers in bytes. Defaults to 16384.
        port (int): SSH port to use for connections. Defaults to 22.

    **Examples:**

    .. code:: python

        # Copy files from one host to another
        yield Copy(
            srcpaths='/home/user/*.txt',
            dstpath='/home/user/',
            src_hosts=["10.156.135.16"],
            dst_hosts=["10.156.135.17"],
            recurse=True
        )
        # Copy multiple files between hosts
        yield Copy(
            srcpaths=['/var/log/app.log', '/tmp/debug.log'],
            dstpath='backup-server:/backup/logs/',
            src_hosts=["host1", "host2"],
            dst_hosts=["backup-server"]
        )

    Note:
        - The copy operation requires proper SSH credentials and permissions on both source and destination hosts
        - Wildcard patterns are supported in source paths
        - For host-to-host copies, the operation will be executed from the destination host
    """

    def __init__(self,
                 srcpaths: Union[str, List[str]],
                 dstpath: str,
                 src_hosts: List[str] = None,
                 dst_hosts: List[str] = None,
                 preserve: bool = False,
                 recurse: bool = False,
                 block_size: int = 16384,
                 port: int = 22):
        self.srcpaths = srcpaths
        self.dstpath = dstpath
        self.src_hosts = src_hosts
        self.dst_hosts = dst_hosts
        self.preserve = preserve
        self.recurse = recurse
        self.block_size = block_size
        self.port = port

    def __repr__(self):
        return f"Copy(srcpaths={self.srcpaths!r}, dstpath={self.dstpath!r}, src_hosts={self.src_hosts!r}, dst_hosts={self.dst_hosts!r}, preserve={self.preserve}, recurse={self.recurse})"

    @staticmethod
    async def _copy_callback(host_info, global_info, command, cp, caller):
        """Static callback method for file copy between hosts"""

        # Validate host_info (matching Download error handling)
        required_keys = ['host', 'username', 'password']
        for key in required_keys:
            if key not in host_info or host_info[key] is None:
                raise ValueError(f"Missing or invalid value for '{key}' in host_info.")

        # Validate caller attributes (matching Download error handling)
        if caller.srcpaths is None:
            raise ValueError("The 'srcpaths' attribute of the caller cannot be None.")
        if caller.dstpath is None:
            raise ValueError("The 'dstpath' attribute of the caller cannot be None.")

        # This operation should run on destination hosts to pull files from source hosts
        is_dest_host = (caller.dst_hosts is None or
                        not caller.dst_hosts or
                        host_info["host"] in caller.dst_hosts)

        if not is_dest_host:
            return  # Only run on destination hosts

        try:
            # Create connection parameters for destination host
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

            print(f"Connecting to destination host {host_info['host']} for copy operation")

            async with asyncssh.connect(**connect_kwargs) as conn:
                print(f"Connected successfully to destination host {host_info['host']}")

                # Prepare source paths with proper host prefixes
                if isinstance(caller.srcpaths, list):
                    srcpaths = []
                    for srcpath in caller.srcpaths:
                        # Add source host prefix if not already present
                        if ':' not in srcpath and caller.src_hosts:
                            # Use the first source host if multiple specified
                            source_host = caller.src_hosts[0]
                            srcpaths.append(f"{source_host}:{srcpath}")
                        else:
                            srcpaths.append(srcpath)
                else:
                    if ':' not in caller.srcpaths and caller.src_hosts:
                        source_host = caller.src_hosts[0]
                        srcpaths = f"{source_host}:{caller.srcpaths}"
                    else:
                        srcpaths = caller.srcpaths

                # Prepare destination path (local to destination host)
                if ':' in caller.dstpath:
                    # Extract path if destination has host prefix
                    dstpath = caller.dstpath.split(':', 1)[1]
                else:
                    dstpath = caller.dstpath

                print(f"Copying from source: {srcpaths} -> destination: {dstpath} on {host_info['host']}")

                # Perform the SCP copy operation (pulling from source to destination)
                await asyncssh.scp(
                    srcpaths,
                    dstpath,
                    preserve=caller.preserve,
                    recurse=caller.recurse,
                    block_size=caller.block_size
                )

                print(f"Copy completed successfully to destination host {host_info['host']}")

        except (OSError, asyncssh.Error) as exc:
            print(f'SCP copy failed on destination host {host_info["host"]}: {str(exc)}')
            # Provide more detailed error information
            if "Permission denied" in str(exc):
                print(f"Check that user {host_info.get('username', 'current user')} has:")
                print(f" - Read permission on source paths: {caller.srcpaths}")
                print(f" - Write permission on destination path: {caller.dstpath}")
                print(f" - Proper SSH key authentication configured")
            raise

    def execute(self):
        """
        Execute the copy operation between hosts.

        Yields:
            Operation: An Operation object that handles the asynchronous copy process.

        Returns:
            Operation: The operation object with executed and changed flags set to True.
        """
        r = yield Operation(f"{self}", local=True, callback=self._copy_callback, caller=self)
        r.executed = True
        r.changed = True
        return r