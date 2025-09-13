import asyncssh

from reemote.operation import Operation


async def get_file(host_info, sudo_info, command, cp, caller):
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
    def __init__(self,
                 path: str,
                 host: str,):
        self.path = path
        self.host = host
    def execute(self):
        r = yield Operation(f"get file {self.path}", local=True, callback=get_file, caller=self)
