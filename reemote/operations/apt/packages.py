# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from typing import List
from reemote.operation_packages import Operation_packages
from reemote.commands.apt.install import Install
from reemote.commands.apt.remove import Remove
from reemote.facts.apt.get_packages import Get_packages


class Packages(Operation_packages):
    """
    A class to manage package operations on a remote system using [apt](file:///home/kim/reemote/reemote/operations/builtin/apt.py#L0-L470).

    This class provides functionality to install or remove packages on Debian/Ubuntu systems
    using the [apt](file:///home/kim/reemote/reemote/operations/builtin/apt.py#L0-L470) package manager. It allows configuration of execution privileges and
    safety guards for package operations.

    Attributes:
        packages (List[str]): List of package names to install or remove.
        present (bool): If `True`, packages will be installed; if `False`, packages will be removed.
        guard (bool): If `False` the commands will not be executed.
        sudo (bool): If `True`, the commands will be executed with [sudo](file:///home/kim/reemote/reemote/command.py#L11-L11) privileges.
        su (bool): If `True`, the commands will be executed with [su](file:///home/kim/reemote/reemote/command.py#L12-L12) privileges.

    **Examples:**

    .. code:: python

        # Install packages on all hosts
        r = yield Packages(["nginx", "curl"], present=True)
        # Check if the operation was successful
        if r.cp.return_code == 0:
            print("Packages installed successfully")

        # Remove packages on all hosts
        r = yield Packages(["nginx", "curl"], present=False)
        # Check if the operation was successful
        if r.cp.return_code == 0:
            print("Packages removed successfully")

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Commands are constructed based on the [present](file:///home/kim/reemote/reemote/operation_packages.py#L30-L30) flag to determine whether to install or remove packages.
        - Uses `apt install` and `apt remove` commands internally for package management.
        - Inherits from [Operation_packages](file:///home/kim/reemote/reemote/operation_packages.py#L4-L89) base class.
    """


    def __init__(self,
                 packages: List[str],
                 present: bool,
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):
        super().__init__(packages, present, guard, sudo, su)

    def get_packages(self):
        return Get_packages()

    def install_packages(self, packages=None,guard=None,present=None,sudo=None,su=None):
        return Install(self.packages, self.guard and self.present, self.sudo, self.su)

    def remove_packages(self, packages=None,guard=None,present=None,sudo=None,su=None):
        return Remove(self.packages, self.guard and not self.present, self.sudo, self.su)
