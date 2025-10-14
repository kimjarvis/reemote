# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command
import os
import tempfile


class Tempfile:
    """
    A class to encapsulate the functionality of creating temporary files and directories.
    This class mimics the behavior of the ansible.builtin.tempfile module, creating temporary
    files or directories with customizable prefix, suffix, and location.

    Attributes:
        path (str): Location where temporary file or directory should be created.
                   If not specified, the default system temporary directory will be used.
        prefix (str): Prefix of file/directory name created by module.
        suffix (str): Suffix of file/directory name created by module.
        state (str): Whether to create file or directory. Choices are "file" or "directory".
        guard (bool): If `False` the commands will not be executed.
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
        su (bool): If `True`, the commands will be executed with `su` privileges.

    **Examples:**

    .. code:: python

        # Create temporary build directory
        r = yield Tempfile(state="directory", suffix="build")

        # Create temporary file
        r = yield Tempfile(state="file", suffix="temp")
        # The path is available in the result
        print(r.path)

        # Create a temporary file with a specific prefix
        r = yield Tempfile(state="file", suffix="txt", prefix="myfile_")

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Files/directories created are accessible only by creator.
        - For world-accessible files, use the File class after creation.
        - Commands are constructed based on the `sudo`, and `su` flags.
    """

    def __init__(self,
                 state: str = "file",
                 path: str = None,
                 prefix: str = "ansible.",
                 suffix: str = "",
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):

        self.state = state
        self.path = path
        self.prefix = prefix
        self.suffix = suffix
        self.guard = guard
        self.sudo = sudo
        self.su = su

    def __repr__(self):
        return (f"Tempfile(state={self.state!r}, "
                f"path={self.path!r}, "
                f"prefix={self.prefix!r}, "
                f"suffix={self.suffix!r}, "
                f"guard={self.guard!r}, "
                f"sudo={self.sudo!r}, su={self.su!r})")

    def execute(self):
        # Determine the directory where the tempfile should be created
        temp_dir = self.path or tempfile.gettempdir()

        # Construct the mktemp command
        if self.state == "directory":
            cmd = f"mktemp -d"
        else:
            cmd = f"mktemp"

        # Add prefix and suffix options
        if self.prefix:
            cmd += f" --prefix={self.prefix}"
        if self.suffix:
            cmd += f" --suffix={self.suffix}"

        # Add directory option
        cmd += f" {temp_dir}/XXXXXX"

        # Execute the command
        r = yield Command(cmd, guard=self.guard, sudo=self.sudo, su=self.su)
        r.changed = True
        # Store the path in the result
        r.path = r.cp.stdout.strip() if r.cp.stdout else None