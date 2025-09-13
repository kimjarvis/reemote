
class Touch:
    """
        A class to encapsulate the functionality of the `touch` and `rm` command in Unix-like operating systems.
        It allows users to specify a target file to be created or removed,
        additional command-line options, and the ability to execute the command with elevated privileges (`sudo`).

        Attributes:
            path (str): The file or directory whose ownership is to be changed.
            present (bool): Indicates whether the file should exist (`True`) or not (`False`) on the system. If `True`, the file will be created if it does not exist. If `False`, the file will be removed if it exists.
            guard (bool): If `False` the commands will not be executed.
            sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
            su (bool): If `True`, the commands will be executed with `su` privileges.
    """
    def __init__(self,
                 path: str,
                 present: bool,
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):

        self.path = path
        self.present = present
        self.guard = guard
        self.sudo = sudo
        self.su = su

        command = "touch"

        op = []
        op.append(command)
        op.append(path)
        self.touch = " ".join(op)

    def __repr__(self):
        return (f"Touch(path={self.path!r}, "
                f"present={self.present!r}, "
                f"guard={self.guard!r}, "
                f"sudo={self.sudo!r}, su={self.su!r})")

    def execute(self):
        r0 = yield self.guard, f"composite {self}"
        r0.executed = self.guard
        _sudo = "sudo -S " if self.sudo else ""
        _su: str = "su" if self.su else ""

        # Get initial file info
        r1 = yield self.guard, f'{_sudo}{_su}"ls -l {self.path}"'
        # print(r1)

        # Execute chown command
        r2 = yield self.guard, f'{_sudo}{_su}"{self.touch}"'
        # print(r2)

        # Get final file info to check if changed
        r3 = yield self.guard, f'{_sudo}{_su}"ls -l {self.path}"'
        # print(r3)

        # Set changed flag if the output differs
        # print(r1)
        # print(r3)
        if self.guard:
            if r1.cp.stdout != r3.cp.stdout:
                r2.changed = True
                r0.changed = True
