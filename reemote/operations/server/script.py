class Script:
    """
    A class to encapsulate the functionality of shell scripts in Unix-like operating systems.
    It allows users to specify a shell script that is executed on all hosts.
    additional command-line options, and the ability to execute the command with elevated privileges (`sudo`).

    Attributes:
        text (str): The shell script.
        guard (bool): If `False` the commands will not be executed.
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
        su (bool): If `True`, the commands will be executed with `su` privileges.

    **Examples:**

    .. code:: python

        from reemote.operations.server.script import Script
        # Execute a shell command on all hosts
        yield Script(text="echo Hello World")
        # The result is available in stdout
        print(r.cp.stdout)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Commands are constructed based on the `sudo`, and `su` flags.
    """
    def __init__(self, user: str=None, password: str=None):
        self.user = user
        self.password = password

    def __init__(self, text: str = None,
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):
        self.text = text
        self.guard = guard
        self.sudo = sudo
        self.su = su

    def __repr__(self):
        return (f"Script("
                f"text={self.text!r}, "
                f"guard={self.guard!r}, "
                f"sudo={self.sudo!r}, su={self.su!r})")

    def execute(self):
        from reemote.operations.server.shell import Shell
        from reemote.operations.sftp.write_file import Write_file
        from reemote.operations.sftp.chmod import Chmod
        from reemote.operations.sftp.remove import Remove

        yield Remove(
            path='/tmp/script.sh',
        )
        yield Write_file(path='/tmp/script', text=f'{self.txt}')
        yield Chmod(
            path='/tmp/script.sh',
            mode=0o755,
        )
        yield Shell("bash /tmp/script.sh", su=self.su)
