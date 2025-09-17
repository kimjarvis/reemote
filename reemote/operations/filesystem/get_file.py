import asyncssh

from reemote.operation import Operation


async def get_file(host_info, global_info, command, cp, caller):
    # Initialize file_content to None
    file_content = None

    # Only execute on the first host in the inventory
    if host_info["host"] == caller.host:
        async def run_client():
            nonlocal file_content  # Use nonlocal to modify the outer variable
            try:
                async with asyncssh.connect(**host_info) as conn:
                    async with conn.start_sftp_client() as sftp:
                        # Open the remote file and read its contents
                        async with sftp.open(caller.path, 'r') as remote_file:
                            file_content = await remote_file.read()
            except (OSError, asyncssh.Error) as exc:
                print('SFTP operation failed:', str(exc))
                raise  # Re-raise the exception to handle it in the caller

        try:
            await run_client()
        except Exception as e:
            print(f"An error occurred: {e}")
            return None  # Return None or handle the error as needed

        return file_content  # Return the actual file content


class Get_file:
    """
    A class to encapsulate the functionality of sftp get in Unix-like operating systems.
    It allows users to specify a target file to be copied from a host.
    The content of the file is available as stdout.

    Attributes:
        path (str): The file or directory whose ownership is to be changed.
        host (str): The host form which the file is being copied from.

    **Examples:**

    .. code:: python

        class Get_file_example:
            def execute(self):
                from reemote.operations.filesystem.get_file import Get_file
                # Get file content from a host
                r = yield Get_file(path='example.txt', host='192.168.122.5')
                # The content is available in stdout
                print(r.cp.stdout)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
    """
    def __init__(self,
                 path: str,
                 host: str,):
        self.path = path
        self.host = host

    def __repr__(self):
        return (f"Get_file(path={self.path!r}, "
                f"host={self.host!r})")

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=get_file, caller=self)
        r.executed = True
        r.changed = False