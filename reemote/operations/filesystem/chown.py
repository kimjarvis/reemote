import asyncssh
from reemote.operation import Operation
from reemote.result import Result


class Chown:
    """
    A class to implement chown operations on a directory in Unix-like operating systems.
    """

    def __init__(self,
                 path: str,
                 owner: str,
                 group: str,
                 ):
        self.path = path
        self.owner = owner
        self.group = group


    def __repr__(self):
        return (
            f"Directory(path={self.path!r}, "
            f"owner={self.owner!r}, "
            f"group={self.group!r}, "
            ")"
        )


    def execute(self):
        from reemote.operations.server.shell import Shell
        from reemote.operations.sftp.write_file import Write_file
        from reemote.operations.sftp.chmod import Chmod
        from reemote.operations.sftp.remove import Remove

        yield Remove(
            path='/tmp/set_owner.sh',
        )
        yield Write_file(path='/tmp/set_owner.sh', text=f'chown {self.owner}:{self.group} {self.path}')
        yield Chmod(
            path='/tmp/set_owner.sh',
            mode=0o755,
        )
        yield Shell("bash /tmp/set_owner.sh", su=True)
        yield Remove(
            path='/tmp/set_owner.sh',
        )
