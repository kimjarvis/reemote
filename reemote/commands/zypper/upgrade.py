# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command
from reemote.command_upgrade import Command_upgrade
class Upgrade(Command_upgrade):
    """
    Implements package upgrade using the zypper package manager.

    This class extends Command to execute the `zypper upgrade` command for upgrading installed packages.

    Attributes:
        guard: A boolean flag indicating whether the operation should be guarded.
        sudo: A boolean flag to specify if sudo privileges are required.
        su: A boolean flag to specify if the operation should run as su.

    **Examples:**

    .. code:: python

        yield Upgrade()

    """
    def execute(self):
        yield Command(f"zypper dist-upgrade", guard=self.guard, sudo=self.sudo, su=self.su)
