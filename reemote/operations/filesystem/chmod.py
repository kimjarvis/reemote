from reemote.operation import Operation

class Chmod:
    """
    A class to encapsulate the functionality of the `chmod` command in Unix-like operating systems.
    It allows users to specify a target file or directory, along with optional user and group access changes,
    additional command-line options, and the ability to execute the command with elevated privileges (`sudo`).

    Attributes:
        path (str): The file or directory whose ownership is to be changed.
        options (List[str]): Additional command-line options for the `chown` or `chgrp` command.
        guard (bool): If `False` the commands will not be executed.
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
        su (bool): If `True`, the commands will be executed with `su` privileges.

    **Examples:**

    .. code:: python

        class Chmod_example:
            def execute(self):
                from reemote.operations.filesystem.chmod import Chmod
                from reemote.operations.filesystem.touch import Touch
                from reemote.operations.server.shell import Shell
                # Create file on all hosts
                r = yield Touch(path='script.sh', present=True)
                # Change the permissions
                r = yield Chmod(path='script.sh', options="+x")
                # View the permissions
                r = yield Shell("ls -ltr script.sh")
                print(r.cp.stdout)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Commands are constructed based on the `present`, `sudo`, and `su` flags.
    """
    def __init__(self,
                 path: str,
                 options=None,
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):

        self.path = path
        self.options = options
        self.guard = guard
        self.sudo = sudo
        self.su = su

        if options is None:
            options = []

        command = "chmod"

        op = []
        op.append(command)
        op.append(options)
        op.append(path)
        self.chmod = " ".join(op)
        # print(self.chmod)

    def __repr__(self):
        return (f"Chmod(path={self.path!r}, "
                f"guard={self.guard!r}, "
                f"sudo={self.sudo!r}, su={self.su!r})")

    def execute(self):
        r0 = yield Operation(f"{self}",composite=True)
        r0.executed = self.guard

        # Get initial file info
        r1 = yield Operation(f'ls -l {self.path}', guard=self.guard, sudo=self.sudo, su=self.su)
        # print(r1)

        # Execute chown command
        r2 = yield Operation(f'{self.chmod}', guard=self.guard, sudo=self.sudo, su=self.su)
        # print(r2)

        # Get final file info to check if changed
        r3 = yield Operation(f'ls -l {self.path}', guard=self.guard, sudo=self.sudo, su=self.su)
        # print(r3)

        # Set changed flag if the output differs
        if self.guard and (r1.cp.stdout != r3.cp.stdout):
            r2.changed = True
            r0.changed = True
