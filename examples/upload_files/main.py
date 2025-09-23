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

from reemote.operations.server.shell import Shell
from reemote.operations.filesystem.mkdir import Mkdir
from reemote.operations.filesystem.makedirs import Makedirs
from reemote.operations.filesystem.read_file import Read_file
from reemote.operations.filesystem.write_file import Write_file
from reemote.operations.filesystem.mput_files import Mput_files
from reemote.operations.filesystem.mget_files import Mget_files

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


def my_progress_callback(src_path, dst_path, copied_bytes, total_bytes):
    """Progress callback with ASCII progress bar"""
    if total_bytes and total_bytes > 0:
        bar_length = 40
        filled_length = int(bar_length * copied_bytes // total_bytes)
        bar = '█' * filled_length + '-' * (bar_length - filled_length)
        percentage = (copied_bytes / total_bytes) * 100
        print(f'\r{bar} {percentage:.1f}% | {copied_bytes}/{total_bytes} bytes', end='', flush=True)

        # Print newline when transfer completes
        if copied_bytes == total_bytes:
            print()

class Deployment:
    def execute(self):
        if False:
            r = yield Shell("echo Hello World!")
            print(r.cp.stdout)

            # Get file content from a host
            r = yield Read_file(path='example.txt', hosts=['192.168.122.5'])
            print(r)
            # The content is available in stdout
            print(r.cp.stdout)

            r = yield Write_file(path='example.txt', text='Hello World!')
            print(r)
            # Verify the file content on the hosts
            r = yield Shell("cat example.txt")
            print(r.cp.stdout)

            # # Create a directory on the host
            r = yield Mkdir(path='/home/user/hfs', attrs=SFTPAttrs(permissions=0o755),hosts=["10.156.135.16"])
            print(r)
            # # Verify the file content on the hosts

            r=yield Makedirs(
                path='/home/user/hfs/new_directory/a/b/c/d/e/f/g',
                attrs=asyncssh.SFTPAttrs(permissions=0o755),
                hosts=["10.156.135.16"],
                exist_ok=False
            )
            print(r)

        # yield Makedirs(path='/home/user/hfs/subdir1/subdir2',
        #                attrs=SFTPAttrs(permissions=0o755),
        #                exist_ok=True,
        #                hosts=["10.156.135.16", "10.156.135.17"])
        #
        # dir='/home/user/dir2'
        # r = yield Mkdir(path=dir, attrs=SFTPAttrs(permissions=0o755))
        # r = yield Mput_files(localpaths='~/reemote/development/hfs/*', remotepath=dir)
        # r = yield Shell(f"tree {dir}")
        # print(r.cp.stdout)

        remote_dir = '/home/user/hfs'
        local_dir = '/home/kim/reemote/development/output'

        r = yield Mget_files(
            remotepaths=f"{remote_dir}/",
            localpath=local_dir,
            preserve=True,
            recurse=True,
            progress_handler=my_progress_callback
        )
        r = yield Shell(f"ls -la {local_dir}")
        print(r.cp.stdout)

async def main():
    responses = await execute(inventory(), Deployment())
    print(produce_table(produce_json(responses)))
    print(produce_output_table(produce_json(responses)))

if __name__ == "__main__":
    asyncio.run(main())
