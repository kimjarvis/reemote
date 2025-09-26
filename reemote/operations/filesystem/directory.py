import asyncssh
from reemote.operation import Operation
from reemote.result import Result


class Directory:
    """
    A class to encapsulate idempotent operations on a directory in Unix-like operating systems.

    Attributes:
        path (str): The directory path to create or remove.
        attrs (str): asyncssh SFTPAttrs object for directory attributes.
        present (bool): Whether the directory should exist or not.

    **Examples:**

    .. code:: python

        yield Directory(
            path='/home/user/hfs',
            present=True,
            attrs=asyncssh.SFTPAttrs(),
        )

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        If hosts is None or empty, the operation will execute on the current host.
    """

    def __init__(self,
                 path: str,
                 present: bool,
                 # attrs: asyncssh.SFTPAttrs = None,
                 ):
        self.path = path
        self.present = present
        # self.attrs = attrs

    def __repr__(self):
        return (
                f"Directory(path={self.path!r}, "
                # f"attrs={self.attrs!r}, "
                f"present={self.present!r}, "
                )

    def execute(self):
        from reemote.facts.sftp.isdir import Isdir
        from reemote.operations.sftp.mkdir import Mkdir
        from reemote.operations.sftp.rmdir import Rmdir
        r0=Result()
        r1=Result()
        r2=Result()
        r3=Result()
        r0 = yield Operation(f"{self}",composite=True)
        r0.executed = True
        r0.changed = False

        r1 = yield Isdir(path=self.path)
        r0.executed = True
        r0.changed = False
        found = r1.cp.stdout

        # Add or remove directory based on the `present` flag
        r2.changed = False
        if self.present and not found:
            # r2 = yield Mkdir(path=self.path, attrs=asyncssh.SFTPAttrs())
            r2 = yield Mkdir(path=self.path)
            r3.executed = True
            r2.changed = True

        r3.changed = False
        if (not self.present) and found:
            r3 = yield Rmdir(path=self.path)
            r3.executed = True
            r3.changed = True

        # Set the `changed` flag iff the state has changed
        if r2.changed or r3.changed:
            r0.executed = True
            r0.changed = True
