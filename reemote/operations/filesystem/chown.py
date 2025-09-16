from reemote.operation import Operation

class Chown:
    """
    A class to encapsulate the functionality of the `chown` or `chgrp` command in Unix-like operating systems.
    It allows users to specify a target file or directory, along with optional user and group ownership changes,
    additional command-line options, and the ability to execute the command with elevated privileges (`sudo`).

    Attributes:
        path (str): The file or directory whose ownership is to be changed.
        user (Optional[str]): The new user owner. Defaults to `None`.
        group (Optional[str]): The new group owner. Defaults to `None`.
        options (List[str]): Additional command-line options for the `chown` or `chgrp` command.
        guard (bool): If `False` the commands will not be executed.
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
        su (bool): If `True`, the commands will be executed with `su` privileges.

    **Examples:**

    .. code:: python

        class Chown_example:
            def execute(self):
                from reemote.operations.filesystem.chown import Chown
                from reemote.operations.filesystem.mkdir import Mkdir
                from reemote.operations.server.shell import Shell
                # Create directory on all hosts
                r = yield Mkdir(path='mydir', present=True)
                # Change the ownership
                r = yield Chown(path='mydir', user="root", group="root", su=True)
                # View the ownership
                r = yield Shell("ls -ltr .")
                print(r.cp.stdout)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Commands are constructed based on the `present`, `sudo`, and `su` flags.

    """
    def __init__(self,
                 path: str,
                 user: str | None = None,
                 group: str | None = None,
                 options=None,
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):

        self.path = path
        self.user = user
        self.group = group
        self.options = options
        self.guard = guard
        self.sudo = sudo
        self.su = su

        if options is None:
            options = []

        command = "chown"
        user_group = None

        if user and group:
            user_group = f"{user}:{group}"
        elif user:
            user_group = user
        elif group:
            command = "chgrp"
            user_group = group
        else:
            raise ValueError("Either user or group must be specified")

        op = []
        op.append(command)
        op.extend(options)
        op.append(user_group)
        op.append(path)
        self.chown = " ".join(op)

    def __repr__(self):
        return (f"Chown(path={self.path!r}, "
                f"user={self.user!r}, group={self.group!r}, "
                f"guard={self.guard!r}, "
                f"sudo={self.sudo!r}, su={self.su!r})")

    def execute(self):
        r0 = yield Operation(f"{self}",composite=True)
        r0.executed = self.guard

        # Get initial file info
        r1 = yield Operation(f'ls -ld {self.path}', guard=self.guard, sudo=self.sudo, su=self.su)
        # print(r1)

        # Execute chown command
        r2 = yield Operation(f'{self.chown}', guard=self.guard, sudo=self.sudo, su=self.su)
        # print(r2)

        # Get final file info to check if changed
        r3 = yield Operation(f'ls -ld {self.path}', guard=self.guard, sudo=self.sudo, su=self.su)
        # print(r3)

        # Set changed flag if the output differs
        if self.guard and (r1.cp.stdout != r3.cp.stdout):
            r2.changed = True
            r0.changed = True
