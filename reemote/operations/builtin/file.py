# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class File:
    """
    A class to manage files and file properties on Unix-like operating systems.
    This class provides functionality similar to Ansible's builtin.file module,
    allowing management of file attributes, permissions, ownership, and states.

    Attributes:
        path (str): Path to the file being managed.
        state (str): Desired state of the file (absent, directory, file, hard, link, touch).
        owner (str): Name of the user that should own the file.
        group (str): Name of the group that should own the file.
        mode (str): Permissions for the file.
        src (str): Source path for links.
        recurse (bool): Recursively set attributes on directory contents.
        force (bool): Force creation of links.
        follow (bool): Follow symbolic links.
        unsafe_writes (bool): Allow unsafe write operations.
        seuser (str): SELinux user context.
        serole (str): SELinux role context.
        setype (str): SELinux type context.
        selevel (str): SELinux level context.
        attributes (str): File attributes to set.
        modification_time (str): Modification time to set.
        access_time (str): Access time to set.
        modification_time_format (str): Format for modification time.
        access_time_format (str): Format for access time.
        guard (bool): If False, commands will not be executed.

    **Examples:**

    .. code:: python

        # Change file ownership, group and permissions
        yield File(path="/etc/foo.conf", owner="foo", group="foo", mode="0644")

        # Create a symbolic link
        yield File(src="/file/to/link/to", dest="/path/to/symlink", state="link")

        # Create a directory if it does not exist
        yield File(path="/etc/some_directory", state="directory", mode="0755")

        # Remove file (delete file)
        yield File(path="/etc/foo.txt", state="absent")

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Commands are constructed based on the provided parameters and state.
        - Supports various file operations including creation, deletion, permission changes, and link management.
    """

    def __init__(self,
                 path: str,
                 state: str = None,
                 owner: str = None,
                 group: str = None,
                 mode: str = None,
                 src: str = None,
                 dest: str = None,
                 name: str = None,
                 recurse: bool = False,
                 force: bool = False,
                 follow: bool = True,
                 unsafe_writes: bool = False,
                 seuser: str = None,
                 serole: str = None,
                 setype: str = None,
                 selevel: str = None,
                 attributes: str = None,
                 attr: str = None,
                 modification_time: str = None,
                 access_time: str = None,
                 modification_time_format: str = "%Y%m%d%H%M.%S",
                 access_time_format: str = "%Y%m%d%H%M.%S",
                 guard: bool = True):

        # Handle aliases
        if dest:
            path = dest
        if name:
            path = name
        if attr:
            attributes = attr

        self.path = path
        self.state = state
        self.owner = owner
        self.group = group
        self.mode = mode
        self.src = src
        self.recurse = recurse
        self.force = force
        self.follow = follow
        self.unsafe_writes = unsafe_writes
        self.seuser = seuser
        self.serole = serole
        self.setype = setype
        self.selevel = selevel
        self.attributes = attributes
        self.modification_time = modification_time
        self.access_time = access_time
        self.modification_time_format = modification_time_format
        self.access_time_format = access_time_format
        self.guard = guard

    def __repr__(self):
        return (f"File(path={self.path!r}, "
                f"state={self.state!r}, "
                f"owner={self.owner!r}, "
                f"group={self.group!r}, "
                f"mode={self.mode!r}, "
                f"src={self.src!r}, "
                f"recurse={self.recurse!r}, "
                f"force={self.force!r}, "
                f"follow={self.follow!r}, "
                f"unsafe_writes={self.unsafe_writes!r})")

    def execute(self):
        # Build the command based on parameters
        cmd_parts = [
            "touch" if self.state == "touch" else "mkdir -p" if self.state == "directory" else "rm -rf" if self.state == "absent" else ""]

        # For file management, we'll use a combination of commands
        if self.state == "link" and self.src:
            cmd_parts = ["ln", "-sf" if self.force else "-s", self.src, self.path]
        elif self.state == "hard" and self.src:
            cmd_parts = ["ln", "-f" if self.force else "", self.src, self.path]
        elif self.state == "directory":
            cmd_parts = ["mkdir", "-p", self.path]
        elif self.state == "absent":
            cmd_parts = ["rm", "-rf", self.path]
        elif self.state == "touch":
            cmd_parts = ["touch", self.path]
        else:
            # Default file operation
            cmd_parts = ["test", "-e", self.path, "||", "touch", self.path]

        # Add ownership changes if specified
        if self.owner or self.group:
            chown_cmd = ["chown"]
            if self.recurse and self.state == "directory":
                chown_cmd.append("-R")
            owner_group = f"{self.owner or ''}{':' + self.group if self.group else ''}"
            if owner_group:
                chown_cmd.extend([owner_group, self.path])
                cmd_parts.extend(["&&"] + chown_cmd)

        # Add permission changes if specified
        if self.mode:
            chmod_cmd = ["chmod"]
            if self.recurse and self.state == "directory":
                chmod_cmd.append("-R")
            chmod_cmd.extend([self.mode, self.path])
            cmd_parts.extend(["&&"] + chmod_cmd)

        # Join command parts
        cmd = " ".join(filter(None, cmd_parts)).strip()

        # Execute the command
        r = yield Command(cmd, guard=self.guard)
        r.changed = True