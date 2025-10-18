# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command_remove import Command_remove
from reemote.command import Command

class Remove(Command_remove):
    """
    Implements package removal using the pipx package manager.

    This class extends Command to execute the `pipx uninstall` command for removing Python applications.

    Attributes:
        packages: List of package names to be removed.
        guard: A boolean flag indicating whether the operation should be guarded.
        sudo: A boolean flag to specify if sudo privileges are required.
        su: A boolean flag to specify if the operation should run as su.

    **Examples:**

    .. code:: python

        yield Remove(packages=['black', 'pytest'])

    """
    def execute(self):
        yield Command(f"pipx uninstall {self.op}", guard=self.guard, sudo=self.sudo, su=self.su)
