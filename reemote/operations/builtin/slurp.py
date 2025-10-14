# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command
import base64


class Slurp:
    """
    A class to encapsulate the functionality of the ansible.builtin.slurp module.
    It fetches a base64-encoded blob containing the data in a remote file.

    This module works like ansible.builtin.fetch but returns base64-encoded content.
    It is used for fetching files from remote nodes and is supported for both
    Unix-like and Windows targets.

    Attributes:
        src (str): The file on the remote system to fetch. This must be a file, not a directory.
        guard (bool): If `False` the commands will not be executed.

    **Examples:**

    .. code:: python

        # Fetch a file from remote nodes
        r = yield Slurp(src="/proc/mounts")
        # The result contains base64-encoded content
        print(r.cp.content)

        # Decode the content
        import base64
        decoded_content = base64.b64decode(r.cp.content).decode('utf-8')
        print(decoded_content)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - This module returns an 'in memory' base64 encoded version of the file
        - This will require at least twice the RAM as the original file size
        - The src parameter is required and must point to a file, not a directory

    """

    def __init__(self,
                 src: str,
                 guard: bool = True):
        """
        Initialize the Slurp command.

        Args:
            src (str): The file on the remote system to fetch. Required.
            guard (bool): If `False` the commands will not be executed. Defaults to True.
        """
        self.src = src
        self.guard = guard

    def __repr__(self):
        return f"Slurp(src={self.src!r}, guard={self.guard!r})"

    def execute(self):
        """
        Execute the slurp command by reading the file and base64 encoding its content.

        Returns:
            Generator yielding a Command object that reads and encodes the file.
        """
        # Construct command to read file and encode it as base64
        cmd = f"cat {self.src} | base64 -w 0"

        # Execute the command
        r = yield Command(cmd, guard=self.guard)

        # Set the return values to match ansible's slurp module
        if hasattr(r, 'cp') and hasattr(r.cp, 'stdout'):
            r.cp.content = r.cp.stdout.strip()
            r.cp.encoding = "base64"
            r.cp.source = self.src
            r.changed = False  # slurp doesn't change the remote file

        return r