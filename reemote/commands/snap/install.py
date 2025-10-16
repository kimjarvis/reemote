# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#

from reemote.command_install import Command_install
from reemote.command import Command

class Install(Command_install):
    """
    Implements package installation using the snap package manager.

    This class extends Command to execute the `snap install` command for installing packages.

    Attributes:
        packages: List of package names to be installed.
        guard: A boolean flag indicating whether the operation should be guarded.
        sudo: A boolean flag to specify if sudo privileges are required.
        su: A boolean flag to specify if the operation should run as su.
        channel: Optional channel to install the snap package from.

    **Examples:**

    .. code:: python

        yield Install(packages=['vlc', 'spotify'])
        yield Install(packages=['myapp'], channel='edge')
    """

    def __init__(self, packages, guard=True, sudo=False, su=False, channel=None):
        super().__init__(packages, guard, sudo, su)
        self.channel = channel

    def execute(self):
        command = f"snap install {self.op}"
        if self.channel:
            command += f" --channel={self.channel}"
        yield Command(command, guard=self.guard, sudo=self.sudo, su=self.su)
