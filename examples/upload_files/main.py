import asyncio
from reemote.execute import execute
from reemote.produce_json import produce_json
from reemote.produce_table import produce_table
from reemote.produce_output_table import produce_output_table
import sys
import asyncssh
from asyncssh import SFTPAttrs

from typing import List, Tuple, Dict, Any

# from reemote.operations.server.shell import Shell
# from reemote.operations.filesystem.mkdir import Mkdir
# from reemote.operations.filesystem.makedirs import Makedirs
# from reemote.operations.filesystem.read_file import Read_file
# from reemote.operations.filesystem.write_file import Write_file
# from reemote.operations.filesystem.mput_files import Mput_files
# from reemote.operations.filesystem.mget_files import Mget_files
# from reemote.operations.filesystem.copy_files import Copy_files
# from reemote.operations.filesystem.mcopy_files import Mcopy_files
# from reemote.operations.filesystem.chdir import Chdir
# from reemote.facts.filesystem.get_cwd import Get_cwd
# from reemote.operations.filesystem.chown import Chown
# from reemote.operations.filesystem.chmod import Chmod
# from reemote.operations.filesystem.touch import Touch
# from reemote.operations.filesystem.upload import Upload
# from reemote.operations.filesystem.download import Download
# from reemote.operations.filesystem.copy import Copy
# from reemote.facts.filesystem.get_stat import Get_stat
# from reemote.facts.filesystem.get_fstat import Get_fstat
# from reemote.facts.filesystem.get_lstat import Get_lstat
# from reemote.operations.filesystem.rmtree import Rmtree
# from reemote.operations.sftp.setstat import Setstat
from reemote.facts.scp.get_statvfs import Get_statvfs

def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
     return [
        (
            {
                'host': '10.156.135.16',  # alpine
                'username': 'user',  # User name
                'password': 'user',  # Password
                'client_keys': ['/home/kim/.ssh/id_ed25519'],
                'known_hosts': '/home/kim/.ssh/known_hosts',
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
                'password': 'user',  # Password
                'client_keys':['/home/kim/.ssh/id_ed25519'],
                'known_hosts': '/home/kim/.ssh/known_hosts',
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

        # remote_dir = '/home/user/hfs'
        # local_dir = '/home/kim/reemote/development/output'
        #
        # r = yield Mget_files(
        #     remotepaths=f"{remote_dir}/",
        #     localpath=local_dir,
        #     preserve=True,
        #     recurse=True,
        #     progress_handler=my_progress_callback
        # )
        # r = yield Shell(f"ls -la {local_dir}")
        # print(r.cp.stdout)


        # src_dir = '/home/user/'
        # dst_dir = '/home/user/backup/'
        # r = yield Mkdir(path=dst_dir, attrs=SFTPAttrs(permissions=0o755), hosts=["10.156.135.16"])
        #
        # r = yield Mcopy_files(
        #     srcpaths=src_dir + '*.txt',  # Using wildcard pattern
        #     dstpath=dst_dir,
        #     preserve=True,
        #     recurse=True,
        #     progress_handler=my_progress_callback,
        #     hosts=["10.156.135.16"]
        # )
        # r = yield Shell(f"ls {dst_dir}/backup/")
        # print(r.cp.stdout)


        # yield Touch(
        #     path='/home/user/script.sh',
        #     hosts=["10.156.135.16", "10.156.135.17"],
        #     pflags_or_mode='w',  # Equivalent to FXF_WRITE | FXF_CREAT | FXF_TRUNC
        #     attrs=asyncssh.SFTPAttrs(perms=0o755)  # Executable permissions
        # )
        #
        # yield Chmod(
        #     path='/home/user/script.sh',
        #     mode=0o755,
        #     hosts=["10.156.135.16", "10.156.135.17"]
        # )
        #
        # yield Chown(
        #     path='/home/user/script.sh',
        #     owner='kim',
        #     group='root',
        #     hosts=["10.156.135.16", "10.156.135.17"]
        # )

        # yield Download(
        #     srcpaths='/home/user/*.txt',  # Remove the host: prefix
        #     dstpath='/home/kim/',
        #     hosts=["10.156.135.16"],
        #     recurse=True
        # )

        # yield Upload(
        #     srcpaths='/home/kim/inventory_alpine.py',  # Remove the host: prefix
        #     dstpath='/home/user/',
        #     hosts=["10.156.135.16"],
        #     recurse=True
        # )
        #
        # # Copy between remote hosts
        # yield Copy(
        #     srcpaths='/home/user/*.txt',
        #     dstpath='/home/user/',
        #     src_hosts=["10.156.135.16"],
        #     dst_hosts=["10.156.135.17"],
        #     recurse=True
        # )

        # r = yield Get_cwd(
        #     hosts=["10.156.135.16", "10.156.135.17"]
        # )
        # print(r)

        # r = yield Get_stat(
        #     hosts=["10.156.135.16", "10.156.135.19"],
        #     path="/etc/passwd"
        # )
        # print(r)

        # yield Get_lstat(
        #     hosts=["10.156.135.16", "10.156.135.17"],
        #     file_path="/path/to/symlink.txt"
        # )
        # print(r)

        # Open file handle
        # yield Get_fstat(
        #     hosts=["10.156.135.16", "10.156.135.17"],
        #     file_handle=file_handle
        # )

        # yield Rmtree(path='/home/user/hfs',
        #      ignore_errors=False,
        #      hosts=["10.156.135.16", "10.156.135.17"])


        # yield Setstat(
        #     hosts=["10.156.135.16", "10.156.135.17"],
        #     path="/home/user/example.txt",
        #     attrs={
        #         "permissions": 0o644,
        #         "uid": 1000,
        #         "gid": 1000,
        #         "mtime": 1672531200
        #     }
        # )


        # r = yield Get_statvfs(
        #     hosts=["10.156.135.16", "10.156.135.17"],
        #     path="/home/user"
        # )
        # print(r)

async def main():
    responses = await execute(inventory(), Deployment())
    print(produce_table(produce_json(responses)))
    print(produce_output_table(produce_json(responses)))

if __name__ == "__main__":
    asyncio.run(main())
