import sys

import asyncssh

from reemote.operation import Operation


async def put_file(host_info, sudo_global, command, cp, caller):
    # Initialize file_content to None
    file_content = None

    async def run_client() -> None:
        try:
            # Connect to the SSH server
            async with asyncssh.connect(**host_info) as conn:
                # Start an SFTP session
                async with conn.start_sftp_client() as sftp:
                    # Define the string content to be written
                    content = caller.text

                    # Open the remote file in write mode and write the content
                    async with sftp.open(caller.path, 'w') as remote_file:
                        await remote_file.write(content)

        except (OSError, asyncssh.Error) as exc:
            sys.exit('SFTP operation failed: ' + str(exc))

    try:
        # Run the client coroutine
        await run_client()
    except KeyboardInterrupt:
        sys.exit('Operation interrupted by user.')


class Put_file:
    """
    A class to encapsulate the functionality of sftp put in Unix-like operating systems.
    It allows users to specify a text to copied to file on all hosts.

    Attributes:
        path (str): The file or directory whose content is to be changed.
        text (str): The file content.

    **Examples:**

    .. code:: python

        class Put_file_example:
            def execute(self):
                from reemote.operations.filesystem.put_file import Put_file
                from reemote.operations.server.shell import Shell
                # Create a file from text on all hosts
                r = yield Put_file(path='example.txt', text='Hello World!')
                # Verify the file content on the hosts
                r = yield Shell("cat example.txt")
                print(r.cp.stdout)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:

    """
    def __init__(self,
                 path: str,
                 text: str):
        self.path = path
        self.text = text

    def __repr__(self):
        return (f"Put_file(path={self.path!r}, "
                f"user={self.text!r})")

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=put_file, caller=self)
        r.executed = True
        r.changed = True