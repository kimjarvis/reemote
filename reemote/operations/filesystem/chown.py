import asyncssh
from jinja2.filters import sync_do_reject

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
                 sudo: bool = False,
                 su: bool = False,
                 ):
        self.path = path
        self.owner = owner
        self.group = group
        self.sudo = sudo
        self.su = su

    def __repr__(self):
        return (
            f"Chown(path={self.path!r}, "
            f"owner={self.owner!r}, "
            f"group={self.group!r}, "
            f"sudo={self.sudo!r}, "
            f"su={self.su!r}, "
            ")"
        )


    def execute(self):
        from reemote.operations.server.shell import Shell
        from reemote.operations.sftp.write_file import Write_file
        from reemote.operations.sftp.chmod import Chmod
        from reemote.operations.sftp.remove import Remove
        from reemote.facts.server.get_os import Get_OS

        r = yield Get_OS()
        os = r.cp.stdout
        yield Remove(
            path='/tmp/set_owner.sh',
        )
        yield Write_file(path='/tmp/set_owner.sh', text=f'chown {self.owner}:{self.group} {self.path}')
        yield Chmod(
            path='/tmp/set_owner.sh',
            mode=0o755,
        )
        if "Alpine" not in os:
            yield Shell("bash /tmp/set_owner.sh", sudo=self.sudo, su=self.su)
        else:
            yield Shell("sh -c '/tmp/set_owner.sh'", sudo=self.sudo, su=self.su)
        yield Remove(
            path='/tmp/set_owner.sh',
        )
