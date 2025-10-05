import asyncssh
from reemote.operation import Operation


class Read_file:
    """
    A class to encapsulate the functionality of reading files from Unix-like operating systems.
    It allows users to read the content of a file from a remote system.

    Attributes:
        path (str): The file path to read content from.

    **Examples:**

    .. code:: python

        # Read a file from remote system
        r = yield Read_file(path='example.txt')
        print(r.cp.stdout)  # Contains the file content

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.
    """

    def __init__(self, path: str):
        self.path = path
        self.content = None  # Will store the read content

    def __repr__(self):
        return f"Read_file(path={self.path!r})"

    @staticmethod
    async def _read_file_callback(host_info, global_info, command, cp, caller):
        """Static callback method for file reading"""

        # Validate host_info (matching Write_file error handling)
        required_keys = ['host', 'username', 'password']
        for key in required_keys:
            if key not in host_info or host_info[key] is None:
                raise ValueError(f"Missing or invalid value for '{key}' in host_info.")

        # Validate caller attributes
        if caller.path is None:
            raise ValueError("The 'path' attribute of the caller cannot be None.")

        try:
            # Connect to the SSH server
            async with asyncssh.connect(**host_info) as conn:
                print("Connected successfully. Starting SFTP session...")
                # Start an SFTP session
                async with conn.start_sftp_client() as sftp:
                    print(f"Reading content from {caller.path}...")

                    # Open the remote file in read mode and read the content
                    async with sftp.open(caller.path, 'r') as remote_file:
                        content = await remote_file.read()
                        print(f"Successfully read file {caller.path} from {host_info['host']}")

                        # Store the content in the caller for access later
                        caller.content = content

                        # Also set the command output for compatibility with the operation framework
                        cp.stdout = content
                        cp.stderr = ""
                        cp.returncode = 0

        except FileNotFoundError:
            error_msg = f"File {caller.path} not found on {host_info['host']}"
            print(error_msg)
            cp.stdout = ""
            cp.stderr = error_msg
            cp.returncode = 1
            raise FileNotFoundError(error_msg)
        except PermissionError:
            error_msg = f"Permission denied reading {caller.path} on {host_info['host']}"
            print(error_msg)
            cp.stdout = ""
            cp.stderr = error_msg
            cp.returncode = 1
            raise PermissionError(error_msg)
        except (OSError, asyncssh.Error) as exc:
            error_msg = f'SFTP operation failed on {host_info["host"]}: {str(exc)}'
            print(error_msg)
            cp.stdout = ""
            cp.stderr = error_msg
            cp.returncode = 1
            raise

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._read_file_callback, caller=self)
        r.executed = True
        r.changed = False  # Reading doesn't change the system
        return r