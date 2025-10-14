# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class Fetch:
    """
    A class to encapsulate the functionality of fetching files from remote nodes.
    This module works like copy, but in reverse. It is used for fetching files
    from remote machines and storing them locally in a file tree, organized by hostname.

    Files that already exist at dest will be overwritten if they are different than the src.
    This module is also supported for Windows targets.

    Attributes:
        src (str): The file on the remote system to fetch. This must be a file, not a directory.
        dest (str): A directory to save the file into.
        fail_on_missing (bool): When set to true, the task will fail if the remote file cannot be read.
        flat (bool): Allows you to override the default behavior of appending hostname/path/to/file to the destination.
        validate_checksum (bool): Verify that the source and destination checksums match after the files are fetched.
        guard (bool): If `False` the commands will not be executed.

    **Examples:**

    .. code:: python

        # Store file into /tmp/fetched/host.example.com/tmp/somefile
        r = yield Fetch(src="/tmp/somefile", dest="/tmp/fetched")

        # Specifying a path directly
        r = yield Fetch(src="/tmp/somefile", dest="/tmp/prefix-{}".format(inventory_hostname), flat=True)

        # Specifying a destination path
        r = yield Fetch(src="/tmp/uniquefile", dest="/tmp/special/", flat=True)

        # Storing in a path relative to the playbook
        r = yield Fetch(src="/tmp/uniquefile", dest="special/prefix-{}".format(inventory_hostname), flat=True)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - When running fetch with become, the slurp module will also be used to fetch the contents
          of the file for determining the remote checksum. This effectively doubles the transfer size.
        - Files are organized by hostname in the destination directory unless flat=True is specified.
    """

    def __init__(self,
                 src: str,
                 dest: str,
                 fail_on_missing: bool = True,
                 flat: bool = False,
                 validate_checksum: bool = True,
                 guard: bool = True):

        self.src = src
        self.dest = dest
        self.fail_on_missing = fail_on_missing
        self.flat = flat
        self.validate_checksum = validate_checksum
        self.guard = guard

    def __repr__(self):
        return (f"Fetch(src={self.src!r}, "
                f"dest={self.dest!r}, "
                f"fail_on_missing={self.fail_on_missing!r}, "
                f"flat={self.flat!r}, "
                f"validate_checksum={self.validate_checksum!r}, "
                f"guard={self.guard!r})")

    def execute(self):
        # Construct the ansible fetch command
        cmd_parts = ["ansible.builtin.fetch"]
        cmd_parts.append(f"src={self.src}")
        cmd_parts.append(f"dest={self.dest}")

        if not self.fail_on_missing:
            cmd_parts.append("fail_on_missing=false")

        if self.flat:
            cmd_parts.append("flat=true")

        if not self.validate_checksum:
            cmd_parts.append("validate_checksum=false")

        cmd = " ".join(cmd_parts)
        r = yield Command(cmd, guard=self.guard)
        r.changed = True