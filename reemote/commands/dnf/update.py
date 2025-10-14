# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command_update import Command_update
from reemote.command import Command

class Update(Command_update):
    """
    Implements package index update using the dnf package manager.

    This class extends Command to execute the `dnf check-update` command for updating package indexes.

    Attributes:
        guard: A boolean flag indicating whether the operation should be guarded.
        sudo: A boolean flag to specify if sudo privileges are required.
        su: A boolean flag to specify if the operation should run as su.

    **Examples:**

    .. code:: python

        yield Update()

    """
    def execute(self):
        yield Command(f"dnf check-update", guard=self.guard, sudo=self.sudo, su=self.su)
