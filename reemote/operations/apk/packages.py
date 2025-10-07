from typing import List
from reemote.operation_packages import Operation_packages
from reemote.commands.apk.install import Install
from reemote.commands.apk.remove import Remove
from reemote.facts.apk.get_packages import Get_packages


class Packages(Operation_packages):
    """
    A class to manage package operations on a remote system using `apk`.
    """

    def __init__(self,
                 packages: List[str],
                 present: bool,
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):
        print("trace 01")
        super().__init__(packages, present, guard, sudo, su)
        print("trace 02")

    def get_packages(self):
        print("trace 00")
        return Get_packages()

    def install_packages(self, packages=None,guard=None,present=None,sudo=None,su=None):
        return Install(self.packages, self.guard and self.present, self.sudo, self.su)

    def remove_packages(self, packages=None,guard=None,present=None,sudo=None,su=None):
        print("trace 20")
        return Remove(self.packages, self.guard and not self.present, self.sudo, self.su)
