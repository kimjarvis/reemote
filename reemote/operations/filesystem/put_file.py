import sys

import asyncssh

from reemote.operation import Operation


async def put_file(host_info, sudo_info, command, cp, caller):
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
            path (str): The file or directory whose ownership is to be changed.
            text (str): The file content.
    """
    def __init__(self,
                 path: str,
                 text: str):
        self.path = path
        self.text = text

    def execute(self):
        r = yield Operation(f"put file {self.path}", local=True, callback=put_file, caller=self)
