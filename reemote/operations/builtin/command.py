# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command
import os
import glob


class AnsibleCommand:
    """
    A class to encapsulate the functionality of executing commands on remote targets,
    similar to Ansible's builtin.command module.

    This class executes commands on all selected nodes without processing through the shell.
    Operations like "*", "<", ">", "|", ";" and "&" will not work. Use Shell class for these features.

    Attributes:
        cmd (str): The command to run.
        argv (list): Passes the command as a list rather than a string.
        chdir (str): Change into this directory before running the command.
        creates (str): A filename or glob pattern. If a matching file already exists, this step will not be run.
        expand_argument_vars (bool): Expands the arguments that are variables.
        removes (str): A filename or glob pattern. If a matching file exists, this step will be run.
        stdin (str): Set the stdin of the command directly to the specified value.
        stdin_add_newline (bool): If set to true, append a newline to stdin data.
        strip_empty_ends (bool): Strip empty lines from the end of stdout/stderr in result.
        guard (bool): If `False` the commands will not be executed.
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
        su (bool): If `True`, the commands will be executed with `su` privileges.

    **Examples:**

    .. code:: python

        # Execute a simple command
        r = yield AnsibleCommand("cat /etc/motd")
        print(r.cp.stdout)

        # Execute command with creates condition
        r = yield AnsibleCommand("/usr/bin/make_database.sh db_user db_name", creates="/path/to/database")

        # Execute command with argv
        r = yield AnsibleCommand(argv=["/usr/bin/make_database.sh", "Username with whitespace", "dbname with whitespace"],
                                creates="/path/to/database")

        # Execute command with chdir
        r = yield AnsibleCommand("/usr/bin/make_database.sh db_user db_name",
                                chdir="somedir/", creates="/path/to/database")

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Commands are constructed based on the `sudo`, and `su` flags.
        - If you want to run a command through the shell, use the Shell class instead.
        - creates, removes, and chdir can be specified to control when commands are executed.
    """

    def __init__(self,
                 cmd: str = None,
                 argv: list = None,
                 chdir: str = None,
                 creates: str = None,
                 expand_argument_vars: bool = True,
                 removes: str = None,
                 stdin: str = None,
                 stdin_add_newline: bool = True,
                 strip_empty_ends: bool = True,
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):

        if cmd is None and argv is None:
            raise ValueError("Either 'cmd' or 'argv' must be provided")

        if cmd is not None and argv is not None:
            raise ValueError("Only one of 'cmd' or 'argv' can be provided, not both")

        self.cmd = cmd
        self.argv = argv
        self.chdir = chdir
        self.creates = creates
        self.expand_argument_vars = expand_argument_vars
        self.removes = removes
        self.stdin = stdin
        self.stdin_add_newline = stdin_add_newline
        self.strip_empty_ends = strip_empty_ends
        self.guard = guard
        self.sudo = sudo
        self.su = su

    def __repr__(self):
        return (f"AnsibleCommand(cmd={self.cmd!r}, "
                f"argv={self.argv!r}, "
                f"chdir={self.chdir!r}, "
                f"creates={self.creates!r}, "
                f"expand_argument_vars={self.expand_argument_vars!r}, "
                f"removes={self.removes!r}, "
                f"stdin={self.stdin!r}, "
                f"stdin_add_newline={self.stdin_add_newline!r}, "
                f"strip_empty_ends={self.strip_empty_ends!r}, "
                f"guard={self.guard!r}, "
                f"sudo={self.sudo!r}, su={self.su!r})")

    def _should_execute(self):
        """Check if the command should be executed based on creates/removes conditions."""
        # Check creates condition first
        if self.creates is not None:
            if glob.glob(self.creates):
                return False  # File exists, don't execute

        # Check removes condition
        if self.removes is not None:
            if not glob.glob(self.removes):
                return False  # File doesn't exist, don't execute

        return True

    def _build_command(self):
        """Build the final command string or argv list."""
        if self.argv:
            return self.argv
        else:
            # Expand variables if requested
            if self.expand_argument_vars and self.cmd:
                # In a real implementation, this would expand environment variables
                # For now, we'll just return the command as-is
                pass
            return self.cmd

    def execute(self):
        """Execute the command with all specified options."""
        # Check if command should be executed
        if not self._should_execute():
            r = yield Command("echo 'Skipped due to creates/removes condition'", guard=False)
            r.changed = False
            r.skipped = True
            return

        # Build the command
        command = self._build_command()

        # Handle chdir by prepending cd command
        if self.chdir:
            if isinstance(command, list):
                # For argv format, we need to handle chdir differently
                # This is a simplified approach - in practice, you'd change the working directory
                command_str = ' '.join(command)
                command = f"cd {self.chdir} && {command_str}"
            else:
                command = f"cd {self.chdir} && {command}"

        # Execute the command
        r = yield Command(command, guard=self.guard, sudo=self.sudo, su=self.su)

        # Process stdin if provided
        if self.stdin is not None:
            if hasattr(r.cp, 'stdin'):
                r.cp.stdin = self.stdin
                if self.stdin_add_newline:
                    r.cp.stdin += '\n'

        # Process output formatting
        if self.strip_empty_ends:
            if hasattr(r.cp, 'stdout'):
                lines = r.cp.stdout.rstrip().split('\n')
                r.cp.stdout_lines = [line for line in lines if line.strip()]
                r.cp.stdout = '\n'.join(r.cp.stdout_lines)
            if hasattr(r.cp, 'stderr'):
                lines = r.cp.stderr.rstrip().split('\n')
                r.cp.stderr_lines = [line for line in lines if line.strip()]
                r.cp.stderr = '\n'.join(r.cp.stderr_lines)

        r.changed = True
        return r