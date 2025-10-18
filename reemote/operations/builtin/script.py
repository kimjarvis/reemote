# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class Script:
    """
    A class to encapsulate the functionality of running local scripts on remote nodes.
    The script is transferred to the remote node and then executed through the shell environment.

    This module does not require Python on the remote system, similar to the raw module.
    It supports both Unix-like and Windows targets.

    Attributes:
        cmd (str): Path to the local script followed by optional arguments.
        chdir (str): Change into this directory on the remote node before running the script.
        creates (str): A filename on the remote node; if it exists, the step will not run.
        decrypt (bool): Controls auto-decryption of source files using vault.
        executable (str): Name or path of an executable to invoke the script with.
        free_form (str): Alternative way to specify the script path and arguments.
        removes (str): A filename on the remote node; if it doesn't exist, the step will not run.
        guard (bool): If `False`, the commands will not be executed.
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
        su (bool): If `True`, the commands will be executed with `su` privileges.

    **Examples:**

    .. code:: python

        # Run a script with arguments (free form)
        r = yield Script("/some/local/script.sh --some-argument 1234")

        # Run a script with arguments (using explicit cmd parameter)
        r = yield Script(cmd="/some/local/script.sh --some-argument 1234")

        # Run a script only if file.txt does not exist on the remote node
        r = yield Script("/some/local/create_file.sh --some-argument 1234",
                        creates="/the/created/file.txt")

        # Run a script only if file.txt exists on the remote node
        r = yield Script("/some/local/remove_file.sh --some-argument 1234",
                        removes="/the/removed/file.txt")

        # Run a script using an executable in a non-system path
        r = yield Script("/some/local/script", executable="/some/remote/executable")

        # Run a script using an executable in a system path
        r = yield Script("/some/local/script.py", executable="python3")

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - It is usually preferable to write Ansible modules rather than pushing scripts.
        - If the path to the local script contains spaces, it needs to be quoted.
        - If the script returns non-UTF-8 data, it should be encoded (e.g., piped through base64).
        - For Windows targets, PowerShell scripts are supported.
        - Commands are constructed based on the `sudo`, and `su` flags.
    """

    def __init__(self,
                 cmd: str = None,
                 chdir: str = None,
                 creates: str = None,
                 decrypt: bool = True,
                 executable: str = None,
                 free_form: str = None,
                 removes: str = None,
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):

        # Handle free_form as the primary input (compatibility with Ansible's free_form)
        self.cmd = cmd or free_form
        self.chdir = chdir
        self.creates = creates
        self.decrypt = decrypt
        self.executable = executable
        self.removes = removes
        self.guard = guard
        self.sudo = sudo
        self.su = su

    def __repr__(self):
        return (f"Script(cmd={self.cmd!r}, "
                f"chdir={self.chdir!r}, "
                f"creates={self.creates!r}, "
                f"decrypt={self.decrypt!r}, "
                f"executable={self.executable!r}, "
                f"removes={self.removes!r}, "
                f"guard={self.guard!r}, "
                f"sudo={self.sudo!r}, "
                f"su={self.su!r})")

    def execute(self):
        # Build the actual command that will be executed remotely
        if not self.cmd:
            raise ValueError("Either 'cmd' or 'free_form' must be specified")

        # Apply creates/removes conditions
        condition_cmd = ""
        if self.creates:
            condition_cmd = f"[ -e '{self.creates}' ] && exit 0; "
        elif self.removes:
            condition_cmd = f"[ ! -e '{self.removes}' ] && exit 0; "

        # Apply chdir if specified
        chdir_cmd = ""
        if self.chdir:
            chdir_cmd = f"cd '{self.chdir}' && "

        # Apply executable if specified
        exec_prefix = ""
        if self.executable:
            exec_prefix = f"{self.executable} "

        # Construct final command
        full_cmd = f"{condition_cmd}{chdir_cmd}{exec_prefix}{self.cmd}"

        # Yield the command for execution
        r = yield Command(full_cmd, guard=self.guard, sudo=self.sudo, su=self.su)
        r.changed = True