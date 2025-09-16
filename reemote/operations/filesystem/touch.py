from reemote.operation import Operation

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


    **Examples:**

    .. code:: python

        class Touch_example:
            def execute(self):
                from reemote.operations.filesystem.touch import Touch
                from reemote.operations.server.shell import Shell
                # Create file on all hosts
                r = yield Touch(path='log.txt', present=True)
                # View the file
                r = yield Shell("ls -ltr log.txt")
                print(r.cp.stdout)
                # Remove file from all hosts
                r = yield Touch(path='log.txt', present=False)
                # Check the file
                r = yield Shell("ls -ltr log.txt")
                print(r.cp.stdout)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Commands are constructed based on the `present`, `sudo`, and `su` flags.
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

    def __repr__(self):
        return (f"Touch(path={self.path!r}, "
                f"present={self.present!r}, "
                f"guard={self.guard!r}, "
                f"sudo={self.sudo!r}, su={self.su!r})")

    def execute(self):
        r0 = yield Operation(f"{self}",composite=True)
        r0.executed = self.guard

        # Get initial file info
        r1 = yield Operation(f'ls -l {self.path}', guard=self.guard, sudo=self.sudo, su=self.su)
        # print(r1)

        # Execute command
        r2 = yield Operation(f'touch {self.path}', guard=self.guard and self.present, sudo=self.sudo, su=self.su)
        # print(r2)

        # Execute command
        r2 = yield Operation(f'rm {self.path}', guard=self.guard and not self.present, sudo=self.sudo, su=self.su)
        # print(r2)

        # Get final file info to check if changed
        r3 = yield Operation(f'ls -l {self.path}', guard=self.guard, sudo=self.sudo, su=self.su)
        # print(r3)

        # Set changed flag if the output differs
        if self.guard and (r1.cp.stdout != r3.cp.stdout):
            r2.changed = True
            r0.changed = True
