from reemote.operations.filesystem.chown import Chown
from reemote.result import Result
from reemote.operation import Operation


class Mkdir:
    """
    A class to manage directory states on a filesystem.

    Attributes:
        path (str): The absolute or relative path of the directory to manage. This is the target directory whose state will be checked or modified.
        present (bool): Indicates whether the directory should exist (`True`) or not (`False`) on the system. If `True`, the directory will be created if it does not exist. If `False`, the directory will be removed if it exists.
        guard (bool): If `False` the commands will not be executed.
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges. Defaults to `False`.
        su (bool): If `True`, the commands will be executed with `su` privileges.

    **Examples:**

    .. code:: python

        class Mkdir_example:
            def execute(self):
                from reemote.operations.filesystem.mkdir import Mkdir
                from reemote.operations.server.shell import Shell
                # Create directory on all hosts
                r = yield Mkdir(path='mydir', present=True)
                # Remove directory from all hosts
                r = yield Mkdir(path='mydir', present=False)
                # View the directory
                r = yield Shell("ls -ltr .")
                print(r.cp.stdout)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.
        It supports creating or removing directories based on the `present` flag and allows privilege escalation via `sudo`.

    Notes:
        - Commands are constructed based on the `present` and `sudo` flags.
        - The `changed` flag is set if the directory state changes after execution.
    """
    def __init__(self,
                 path: str,
                 present: bool,
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False
                 ):
        self.path = path
        self.present = present
        self.guard = guard
        self.sudo = sudo
        self.su = su

    def __repr__(self):
        return (f"Directory(path={self.path!r}, present={self.present!r}, "
                f"guard={self.guard!r}, "
                f"sudo={self.sudo!r}, su={self.su!r})")

    def execute(self):
        r0 = yield Operation(f"{self}",composite=True)
        r0.executed = self.guard

        # Check whether the directory exists
        r1: Result = yield Operation(f"[ -d {self.path} ]", guard=self.guard, sudo=self.sudo, su=self.su)

        # Directory should be present, but it does not exist, so create it
        r2 = yield Operation(f'mkdir -p {self.path}', guard=self.present and r1.cp.returncode != 0 and self.guard, sudo=self.sudo, su=self.su)
        r2.changed = r2.executed

        # Directory should not be present, but it exists, so remove it
        r3 = yield Operation(f'rmdir -p {self.path}', guard=(not self.present) and r1.cp.returncode == 0 and self.guard, sudo=self.sudo, su=self.su)
        r3.changed = r3.executed

        r0.changed = r2.changed or r3.changed
