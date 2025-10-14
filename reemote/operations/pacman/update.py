# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from typing import List
from reemote.operation_update import Operation_update
from reemote.commands.pacman.upgrade import Upgrade
from reemote.facts.pacman.get_packages import Get_packages


class Update(Operation_update):
    """
    A class to manage package update operations on a remote system using `pacman`.

    This class provides functionality to update package databases on Arch Linux systems
    using the pacman package manager. It allows configuration of execution privileges and
    safety guards for package update operations.

    Attributes:
        guard (bool): If `False` the commands will not be executed.
        sudo (bool): If `True`, the commands will be executed with [sudo](file:///home/kim/reemote/reemote/command.py#L11-L11) privileges.
        su (bool): If `True`, the commands will be executed with [su](file:///home/kim/reemote/reemote/command.py#L12-L12) privileges.

    **Examples:**

    .. code:: python

        # Update package databases on all hosts
        r = yield Update()
        # Check if the operation was successful
        if r.cp.return_code == 0:
            print("Package databases updated successfully")

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Uses `pacman -Sy` command internally for updating package databases.
        - Inherits from [Operation_update](file:///home/kim/reemote/reemote/operation_update.py#L0-L63) base class.
        - This operation only updates the package databases but does not upgrade installed packages.
    """


    def __init__(self,
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):
        super().__init__(guard, sudo, su)

    def get_packages(self):
        return Get_packages()

    def update_packages(self, guard=None,sudo=None,su=None):
        return Update(self.guard, self.sudo, self.su)
