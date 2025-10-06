from typing import List
from reemote.packages import Packages
from reemote.commands.dpkg.install import Install
from reemote.commands.dpkg.remove import Remove
from reemote.facts.dpkg.get_packages import Get_packages


class Packages(Packages):
    """
    A class to manage package operations on a remote system using `dpkg`.
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

    def install_packages(self):
        return Install(self.packages, self.guard and self.present, self.sudo, self.su)

    def remove_packages(self):
        return Remove(self.packages, self.guard and not self.present, self.sudo, self.su)