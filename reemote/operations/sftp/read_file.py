import asyncssh
from reemote.operation import Operation


class Read_file:
    """
    A class to encapsulate the functionality of reading files in Unix-like operating systems.
    It allows users to specify a target file to be read from multiple hosts.
    The content of the file is available as stdout.

    Attributes:
        path (str): The file path to read from.

    **Examples:**

    .. code:: python

        # Get file content from specific hosts
        r = yield Read_file(path='example.txt')
        # The content is available in stdout
        print(r.cp.stdout)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.
    """

    def __init__(self, path: str):
        self.path = path

    def __repr__(self):
        return f"Read_file(path={self.path!r})"

    @staticmethod
    async def _read_file_callback(host_info, global_info, command, cp, caller):
        """Static callback method for file reading"""

        # Validate host_info (matching Mget_files error handling)
        required_keys = ['host', 'username', 'password']
        for key in required_keys:
            if key not in host_info or host_info[key] is None:
                raise ValueError(f"Missing or invalid value for '{key}' in host_info.")

        # Validate caller attributes (matching Mget_files error handling)
        if caller.path is None:
            raise ValueError("The 'path' attribute of the caller cannot be None.")

        try:
            # Connect to the SSH server
            async with asyncssh.connect(**host_info) as conn:
                # Start an SFTP session
                async with conn.start_sftp_client() as sftp:
                    # Open the remote file and read its contents
                    async with sftp.open(caller.path, 'r') as remote_file:
                        file_content = await remote_file.read()
                    return file_content
        except (OSError, asyncssh.Error) as exc:
            raise  # Re-raise the exception to handle it in the caller

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._read_file_callback, caller=self)
        r.executed = True
        r.changed = False
        return r