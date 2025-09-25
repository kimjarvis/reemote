from reemote.operation import Operation

class Shell:
    """
    A class to encapsulate the functionality of shell commands in Unix-like operating systems.
    It allows users to specify a shell command that is executed on all hosts.
    additional command-line options, and the ability to execute the command with elevated privileges (`sudo`).

    Attributes:
        cmd (str): The shell command.
        guard (bool): If `False` the commands will not be executed.
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
        su (bool): If `True`, the commands will be executed with `su` privileges.

    **Examples:**

    .. code:: python

        # Execute a shell command on all hosts
        r = yield Shell("echo Hello World")
        # The result is available in stdout
        print(r.cp.stdout)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Commands are constructed based on the `sudo`, and `su` flags.

    """
    def __init__(self,
                 cmd: str,
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):

        self.cmd = cmd
        self.guard = guard
        self.sudo = sudo
        self.su = su

    def __repr__(self):
        return (f"Shell(cmd={self.cmd!r}, "
                f"guard={self.guard!r}, "
                f"sudo={self.sudo!r}, su={self.su!r})")

    def execute(self):
        print(f"{self}")
        r = yield Operation(self.cmd,guard=self.guard,sudo=self.sudo,su=self.su)
        r.changed = True
