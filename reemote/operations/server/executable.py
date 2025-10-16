# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com
# This software is licensed under the MIT License. See the LICENSE file for details.
#
class Executable:
    """
    A class to encapsulate the functionality of shell scripts in Unix-like operating systems.
    It allows users to specify a shell script that is executed on all hosts, with support
    for additional command-line options and the ability to execute the command with
    elevated privileges ([sudo](file:///home/kim/reemote/reemote/command.py#L52-L52) or [su](file:///home/kim/reemote/reemote/command.py#L53-L53)).

    Attributes:
        text (str): The shell script content to be executed.
        guard (bool): If `False`, the commands will not be executed. Defaults to `True`.
        sudo (bool): If `True`, the commands will be executed with [sudo](file:///home/kim/reemote/reemote/command.py#L52-L52) privileges. Defaults to `False`.
        su (bool): If `True`, the commands will be executed with [su](file:///home/kim/reemote/reemote/command.py#L53-L53) privileges. Defaults to `False`.

    Examples:
        >>> from reemote.operations.server.executable import Executable
        >>> # Execute a shell command on all hosts
        >>> yield Executable(text="echo Hello World")
        >>> # The result is available in stdout
        >>> print(r.cp.stdout)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Commands are constructed based on the [sudo](file:///home/kim/reemote/reemote/command.py#L52-L52) and [su](file:///home/kim/reemote/reemote/command.py#L53-L53) flags.
        - The script is written to a temporary file, made executable, and then run.
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
        return (f"Executable("
                f"text={self.text!r}, "
                f"guard={self.guard!r}, "
                f"sudo={self.sudo!r}, su={self.su!r})")

    def execute(self):
        from reemote.operations.server.shell import Shell
        from reemote.operations.sftp.write_file import Write_file
        from reemote.operations.sftp.chmod import Chmod
        from reemote.operations.sftp.remove import Remove

        yield Remove(
            path='/tmp/script',
        )
        yield Write_file(path='/tmp/script', text=f'{self.text}')
        yield Chmod(
            path='/tmp/script',
            mode=0o755,
        )
        yield Shell("env /tmp/script", su=self.su)
