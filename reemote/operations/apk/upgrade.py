# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from typing import List
from reemote.operation_upgrade import Operation_upgrade
from reemote.commands.apk.upgrade import Upgrade
from reemote.facts.apk.get_packages import Get_packages


class Upgrade(Operation_upgrade):
    """
    A class to manage package upgrade operations on a remote system using `apk`.

    This class provides functionality to upgrade packages on Alpine Linux systems
    and allows configuration of execution privileges and safety guards.

    Attributes:
        guard (bool): If `False` the commands will not be executed.
        sudo (bool): If `True`, the commands will be executed with [sudo](file:///home/kim/reemote/reemote/command.py#L11-L11) privileges.
        su (bool): If `True`, the commands will be executed with [su](file:///home/kim/reemote/reemote/command.py#L12-L12) privileges.

    **Examples:**

    .. code:: python

        # Upgrade packages on all hosts
        r = yield Upgrade()
        # Check if the operation was successful
        if r.cp.return_code == 0:
            print("Packages upgraded successfully")

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Commands are constructed based on the [sudo](file:///home/kim/reemote/reemote/command.py#L11-L11), and [su](file:///home/kim/reemote/reemote/command.py#L12-L12) flags.
        - Uses the `apk upgrade` command internally for package management.
        - Inherits from [Operation_upgrade](file:///home/kim/reemote/reemote/operation_upgrade.py#L0-L58) base class.
    """

    def __init__(self,
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):
        super().__init__(guard, sudo, su)

    def get_packages(self):
        return Get_packages()

    def upgrade(self, guard=None,sudo=None,su=None):
        from reemote.commands.apk.upgrade import Upgrade
        return Upgrade(self.guard, self.sudo, self.su)
