import asyncssh
from reemote.operation import Operation
from typing import Union, List, Optional


class Upload:
    """
    A class to encapsulate the functionality of uploading files via SCP.

    Attributes:
        srcpaths (Union[str, List[str]]): The local source file(s) or directory to upload.
        dstpath (str): The remote destination path.
        hosts (list): The list of hosts to which to upload files.
        preserve (bool): Whether to preserve file attributes.
        recurse (bool): Whether to recursively copy directories.
        block_size (int): The block size for file transfers.
        port (int): SSH port to use for connections.

    **Examples:**

    .. code:: python

        yield Upload(
            srcpaths='/home/kim/inventory_alpine.py',  # Remove the host: prefix
            dstpath='/home/user/',
            hosts=["10.156.135.16"],
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
        return f"Upload(srcpaths={self.srcpaths!r}, dstpath={self.dstpath!r}, hosts={self.hosts!r}, preserve={self.preserve}, recurse={self.recurse})"

    @staticmethod
    async def _upload_callback(host_info, global_info, command, cp, caller):
        """Static callback method for file upload"""

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

                        # Handle destination path - remove any host prefix since we're already connected
                        if ':' in caller.dstpath:
                            # Extract path after host: part
                            dstpath = caller.dstpath.split(':', 1)[1]
                        else:
                            dstpath = caller.dstpath

                        print(f"Uploading: {caller.srcpaths} -> {dstpath} on {host_info['host']}")

                        await asyncssh.scp(
                            caller.srcpaths,
                            (conn, dstpath),  # Use connection object instead of host string
                            preserve=caller.preserve,
                            recurse=caller.recurse,
                            block_size=caller.block_size
                        )
                        print(f"Upload completed to {host_info['host']}")

                except (OSError, asyncssh.Error) as exc:
                    print(f'SCP upload failed on {host_info["host"]}: {str(exc)}')
                    raise

            try:
                await run_client()
            except Exception as e:
                print(f"An error occurred on {host_info['host']}: {e}")
                return None

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._upload_callback, caller=self)
        r.executed = True
        r.changed = True