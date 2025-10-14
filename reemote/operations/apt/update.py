# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from typing import List
from reemote.operation_update import Operation_update
from reemote.commands.apt.upgrade import Upgrade
from reemote.facts.apt.get_packages import Get_packages


class Update(Operation_update):
    """
    A class to manage package update operations on a remote system using [apt](file:///home/kim/reemote/reemote/operations/builtin/apt.py#L0-L470).

    This class provides functionality to update package lists on Debian/Ubuntu systems
    using the apt package manager. It allows configuration of execution privileges and
    safety guards for package update operations.

    Attributes:
        guard (bool): If `False` the commands will not be executed.
        sudo (bool): If `True`, the commands will be executed with [sudo](file:///home/kim/reemote/reemote/command.py#L11-L11) privileges.
        su (bool): If `True`, the commands will be executed with [su](file:///home/kim/reemote/reemote/command.py#L12-L12) privileges.

    **Examples:**

    .. code:: python

        # Update package lists on all hosts
        r = yield Update()
        # Check if the operation was successful
        if r.cp.return_code == 0:
            print("Package lists updated successfully")

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Uses `apt update` command internally for updating package lists.
        - Inherits from [Operation_update](file:///home/kim/reemote/reemote/operation_update.py#L0-L63) base class.
        - This operation only updates the package lists but does not upgrade installed packages.
    """

    def __init__(self,
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):
        super().__init__(guard, sudo, su)

    def get_packages(self):
        return Get_packages()

    def update_packages(self, guard=None,sudo=None,su=None):
        from reemote.commands.apt.update import Update
        return Update(self.guard, self.sudo, self.su)
