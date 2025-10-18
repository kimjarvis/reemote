# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command_remove import Command_remove
from reemote.command import Command

class Remove(Command_remove):
    """
    Implements package removal using the pip package manager.

    This class extends Command to execute the `pip uninstall -y` command for removing Python packages.

    Attributes:
        packages: List of package names to be removed.
        guard: A boolean flag indicating whether the operation should be guarded.
        sudo: A boolean flag to specify if sudo privileges are required.
        su: A boolean flag to specify if the operation should run as su.

    **Examples:**

    .. code:: python

        yield Remove(packages=['requests', 'numpy'])

    """
    def execute(self):
        yield Command(f"pip uninstall -y {self.op}", guard=self.guard, sudo=self.sudo, su=self.su)
