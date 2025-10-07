from typing import List
from reemote.operation_packages import Operation_packages
from reemote.commands.yum.install import Install
from reemote.commands.yum.remove import Remove
from reemote.facts.yum.get_packages import Get_packages


class Packages(Operation_packages):
    """
    A class to manage package operations on a remote system using `yum`.
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
