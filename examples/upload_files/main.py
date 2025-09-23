import asyncio
from reemote.execute import execute
from reemote.produce_json import produce_json
from reemote.produce_table import produce_table
from reemote.produce_output_table import produce_output_table
import sys
import asyncssh
from reemote.operation import Operation
from asyncssh import SFTPAttrs

from typing import List, Tuple, Dict, Any

from typing import List, Tuple, Dict, Any

def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
     return [
        (
            {
                'host': '10.156.135.16',  # alpine
                'username': 'user',  # User name
                'password': 'user'  # Password
            },
            {
                'su_user': 'root',
                'su_password': 'root'  # Password
            }
        ),
        (
            {
                'host': '10.156.135.19',  # alpine
                'username': 'user',  # User name
                'password': 'user'  # Password
            },
            {
                'su_user': 'root',
                'su_password': 'root'  # Password
            }
        )
    ]


class Makedirs:
    """
      A class to encapsulate the functionality of makedirs (recursive directory creation).

      Attributes:
          path (str): The directory path to create recursively.
          attrs (SFTPAttrs): asyncssh SFTPAttrs object for directory attributes.
          exist_ok (bool): Whether to raise an error if the target directory already exists.
          host (str): The host on which the directories are to be created.

      Examples:
      .. code:: python
          class Create_dirs_example:
              def execute(self):
                  yield Makedirs(path='/home/user/hfs/subdir1/subdir2',
                               attrs=SFTPAttrs(permissions=0o755),
                               exist_ok=True,
                               host="10.156.135.16")

      Usage:
          This class is designed to be used in a generator-based workflow where commands are yielded for execution.

      Notes:
          This will create all intermediate directories in the path if they don't exist.
    """
    @staticmethod
    async def _makedirs_callback(host_info, global_info, command, cp, caller):
        """Static callback method for directory creation"""
        print("Making directories recursively")
        if caller.host is None or caller.host == host_info["host"]:
            print("Making directories 1")

            async def run_client():
                print("Making directories 2")
                try:
                    async with asyncssh.connect(**host_info) as conn:
                        print("Making directories 3")
                        async with conn.start_sftp_client() as sftp:
                            print("Making directories 4")
                            await sftp.makedirs(path=caller.path, attrs=caller.attrs, exist_ok=caller.exist_ok)
                except (OSError, asyncssh.Error) as exc:
                    print('SFTP makedirs operation failed:', str(exc))
                    raise

            try:
                await run_client()
            except Exception as e:
                print(f"An error occurred: {e}")
                return None

    def __init__(self,
                 path: str,
                 attrs: str = None,
                 exist_ok: bool = False,
                 host: str = None):
        self.path = path
        self.attrs = attrs
        self.exist_ok = exist_ok
        self.host = host

    def __repr__(self):
        return (f"Makedirs(path={self.path!r}, "
                f"attrs={self.attrs!r}, "
                f"exist_ok={self.exist_ok!r}, "
                f"host={self.host!r})")

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._makedirs_callback, caller=self)
        r.executed = True
        r.changed = False

# async def mkdir(host_info, global_info, command, cp, caller):
#     print("Making directory")
#     if caller.host is None or caller.host == host_info["host"]:
#         print("Making directory 1")
#         async def run_client():
#             print("Making directory 2")
#             try:
#                 async with asyncssh.connect(**host_info) as conn:
#                     print("Making directory 3")
#                     async with conn.start_sftp_client() as sftp:
#                         print("Making directory 4")
#                         # Open the remote create the directory with the desired attributes
#                         await sftp.mkdir(path=caller.path, attrs=caller.attrs)
#             except (OSError, asyncssh.Error) as exc:
#                 print('SFTP operation failed:', str(exc))
#                 raise  # Re-raise the exception to handle it in the caller
#
#         try:
#             await run_client()
#         except Exception as e:
#             print(f"An error occurred: {e}")
#             return None  # Return None or handle the error as needed
#
# class Mkdir:
#     """
#     A class to encapsulate the functionality of mkdir get in Unix-like operating systems.
#
#     Attributes:
#         path (str): The file or directory whose ownership is to be changed.
#         attrs (str): asyncssh SFTPAttrs object.
#         host (str): The host on which the directory is to be created.
#                  path: str,
#                  attrs: str,
#                  host: str,):
#
#     **Examples:**
#
#     .. code:: python
#
#         class Get_file_example:
#             def execute(self):
#                 yield Mkdir(path='/home/user/hfs', attrs=SFTPAttrs(permissions=0o755), host="10.156.135.16")
#
#     Usage:
#         This class is designed to be used in a generator-based workflow where commands are yielded for execution.
#
#     Notes:
#     """
#     def __init__(self,
#                  path: str,
#                  attrs: str,
#                  host: str = None,):
#         self.path = path
#         self.attrs = attrs
#         self.host = host
#
#     def __repr__(self):
#         return (f"Mkdir(path={self.path!r}, "
#                 f"attrs={self.attrs!r})"
#                 f"host={self.host!r})")
#
#     def execute(self):
#         r = yield Operation(f"{self}", local=True, callback=mkdir, caller=self)
#         r.executed = True
#         r.changed = False

class Mkdir:
    """
    A class to encapsulate the functionality of mkdir get in Unix-like operating systems.

    Attributes:
        path (str): The file or directory whose ownership is to be changed.
        attrs (str): asyncssh SFTPAttrs object.
        host (str): The host on which the directory is to be created.
                 path: str,
                 attrs: str,
                 host: str,):

    **Examples:**

    .. code:: python

        class Get_file_example:
            def execute(self):
                yield Mkdir(path='/home/user/hfs', attrs=SFTPAttrs(permissions=0o755), host="10.156.135.16")

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
    """
    def __init__(self,
                 path: str,
                 attrs: str,
                 host: str = None,):
        self.path = path
        self.attrs = attrs
        self.host = host

    def __repr__(self):
        return (f"Mkdir(path={self.path!r}, "
                f"attrs={self.attrs!r})"
                f"host={self.host!r})")

    @staticmethod
    async def _mkdir_callback(host_info, global_info, command, cp, caller):
        """Static callback method for directory creation"""
        print("Making directory")
        if caller.host is None or caller.host == host_info["host"]:
            print("Making directory 1")
            async def run_client():
                print("Making directory 2")
                try:
                    async with asyncssh.connect(**host_info) as conn:
                        print("Making directory 3")
                        async with conn.start_sftp_client() as sftp:
                            print("Making directory 4")
                            # Open the remote create the directory with the desired attributes
                            await sftp.mkdir(path=caller.path, attrs=caller.attrs)
                except (OSError, asyncssh.Error) as exc:
                    print('SFTP operation failed:', str(exc))
                    raise  # Re-raise the exception to handle it in the caller

            try:
                await run_client()
            except Exception as e:
                print(f"An error occurred: {e}")
                return None  # Return None or handle the error as needed

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._mkdir_callback, caller=self)
        r.executed = True
        r.changed = False

async def get_file(host_info, global_info, command, cp, caller):
    # Initialize file_content to None
    file_content = None

    if host_info.get("host") is None or host_info["host"] == caller.host:
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

async def put_local(host_info, sudo_global, command, cp, caller):
    # Initialize file_content to None
    file_content = None

    async def run_client() -> None:
        try:
            # Connect to the SSH server
            async with asyncssh.connect(**host_info) as conn:
                # Start an SFTP session
                async with conn.start_sftp_client() as sftp:
                    await sftp.mput(localpaths=caller.localpaths,remotepath=caller.remotepath, recurse=True)

        except (OSError, asyncssh.Error) as exc:
            sys.exit('SFTP operation failed: ' + str(exc))

    try:
        # Run the client coroutine
        await run_client()
    except KeyboardInterrupt:
        sys.exit('Operation interrupted by user.')


class Put_local:
    def __init__(self,
                 localpaths: str,
                 remotepath: str):
        self.localpaths = localpaths
        self.remotepath = remotepath

    def __repr__(self):
        return (f"Put_file(localpaths={self.localpaths!r}, "
                f"remotepath={self.remotepath!r})")

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=put_local, caller=self)
        r.executed = True
        r.changed = True

class Deployment:
    def execute(self):
        # from reemote.operations.filesystem.put_file import Put_file
        # from reemote.operations.server.shell import Shell
        # r = yield Shell("echo Hello World!")
        # print(r.cp.stdout)
        #
        # # Get file content from a host
        # r = yield Get_file(path='example.txt', host='192.168.122.5')
        # # The content is available in stdout
        # print(r.cp.stdout)
        #
        # r = yield Put_file(path='example.txt', text='Hello World!')
        # # Verify the file content on the hosts
        # r = yield Shell("cat example.txt")
        # print(r.cp.stdout)

        # # Create a directory on the host
        # r = yield Mkdir(path='/home/user/hfs', attrs=SFTPAttrs(permissions=0o755),host="10.156.135.16")
        r = yield Mkdir(path='/home/user/hfs1', attrs=SFTPAttrs(permissions=0o755))
        # print(r)
        # # Verify the file content on the hosts

        yield Makedirs(
            path='/home/user/hfs/new_directory/a/b/c/d/e',
            attrs=asyncssh.SFTPAttrs(permissions=0o755),
            hosts=["10.156.135.16"],
            exist_ok=False
        )

        # # Create a file from text on all hosts
        # # The destination directory must exist
        # r = yield Put_local(localpaths='/home/kim/reemote/development/hfs/*', remotepath='/home/user/hfs')
        # # Verify the file content on the hosts
        # r = yield Shell("cat example.txt")
        # print(r.cp.stdout)

async def main():
    responses = await execute(inventory(), Deployment())
    print(produce_table(produce_json(responses)))
    print(produce_output_table(produce_json(responses)))

if __name__ == "__main__":
    asyncio.run(main())
