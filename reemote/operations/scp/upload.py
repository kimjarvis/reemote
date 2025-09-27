import asyncssh
from reemote.operation import Operation
from typing import Union, List, Optional


class Upload:
    """
    A class to encapsulate the functionality of uploading files via SCP.

    Attributes:
        srcpaths (Union[str, List[str]]): The local source file(s) or directory to upload.
        dstpath (str): The remote destination path.
        preserve (bool): Whether to preserve file attributes.
        recurse (bool): Whether to recursively copy directories.
        block_size (int): The block size for file transfers.
        port (int): SSH port to use for connections.

    **Examples:**

    .. code:: python

        yield Upload(
            srcpaths='/home/kim/inventory_alpine.py',
            dstpath='/home/user/',
            recurse=True
        )

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution.

    Notes:
        Supports wildcard patterns and recursive directory copying.
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
        return f"Upload(srcpaths={self.srcpaths!r}, dstpath={self.dstpath!r}, preserve={self.preserve}, recurse={self.recurse})"

    @staticmethod
    async def _upload_callback(host_info, global_info, command, cp, caller):
        """Static callback method for file upload"""

        # Validate host_info (matching Write_file error handling)
        required_keys = ['host', 'username', 'password']
        for key in required_keys:
            if key not in host_info or host_info[key] is None:
                raise ValueError(f"Missing or invalid value for '{key}' in host_info.")

        # Validate caller attributes (matching Write_file error handling)
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
                # Handle destination path - remove any host prefix since we're already connected
                if ':' in caller.dstpath:
                    # Extract path after host: part
                    dstpath = caller.dstpath.split(':', 1)[1]
                else:
                    dstpath = caller.dstpath

                # Perform the SCP upload
                await asyncssh.scp(
                    caller.srcpaths,
                    (conn, dstpath),  # Use connection object instead of host string
                    preserve=caller.preserve,
                    recurse=caller.recurse,
                    block_size=caller.block_size
                )

        except (OSError, asyncssh.Error) as exc:
            raise

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._upload_callback, caller=self)
        r.executed = True
        r.changed = True
        return r