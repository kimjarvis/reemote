import asyncssh
from reemote.operation import Operation
class Write_file:
    """
    A class to encapsulate the functionality of writing files in Unix-like operating systems.
    It allows users to specify text to be written to a file on multiple hosts.

    Attributes:
        path (str): The file path where content is to be written.
        text (str): The file content.
        hosts (list): The list of hosts on which the file is to be written.

    **Examples:**

    .. code:: python

        # Create a file from text on specific hosts
        r = yield Write_file(path='example.txt', text='Hello World!', hosts=['host1', 'host2'])
        # Verify the file content on the hosts
        r = yield Shell("cat example.txt")
        print(r.cp.stdout)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        If hosts is None or empty, the operation will execute on the current host.
    """

    def __init__(self,
                 path: str,
                 text: str,
                 hosts: list = None):
        self.path = path
        self.text = text
        self.hosts = hosts

    def __repr__(self):
        return (f"Write_file(path={self.path!r}, "
                f"text={self.text!r}, "
                f"hosts={self.hosts!r})")

    @staticmethod
    async def _write_file_callback(host_info, global_info, command, cp, caller):
        print(f"{caller}")
        """Static callback method for file writing"""
        # Initialize file_content to None
        file_content = None

        # Check if this host is in the target hosts list or if hosts list is empty/None
        if (caller.hosts is None or
                not caller.hosts or
                host_info["host"] in caller.hosts):

            print(f"Writing file on host {host_info['host']}")

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
                            print(f"Successfully wrote file on {host_info['host']}")

                except (OSError, asyncssh.Error) as exc:
                    print(f'SFTP operation failed on {host_info["host"]}: {str(exc)}')
                    raise

            try:
                # Run the client coroutine
                await run_client()
            except KeyboardInterrupt:
                print('Operation interrupted by user.')
                raise
            except Exception as e:
                print(f"An error occurred on {host_info['host']}: {e}")
                return None

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._write_file_callback, caller=self)
        r.executed = True
        r.changed = True