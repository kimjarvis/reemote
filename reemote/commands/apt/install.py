# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#

from reemote.command_install import Command_install
from reemote.command import Command

class Install(Command_install):
    """
    Implements package installation using the apt package manager.

    This class extends Command to execute the `apt install -y` command for installing packages.

    Attributes:
        packages: List of package names to be installed.
        guard: A boolean flag indicating whether the operation should be guarded.
        sudo: A boolean flag to specify if sudo privileges are required.
        su: A boolean flag to specify if the operation should run as su.

    **Examples:**

    .. code:: python

        yield Install(packages=['vim', 'git'])

    """
    def execute(self):
        yield Command(f"apt install -y {self.op}", guard=self.guard, sudo=self.sudo, su=self.su)
