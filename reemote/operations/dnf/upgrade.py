from typing import List
from reemote.operation_upgrade import Operation_upgrade
from reemote.commands.dnf.upgrade import Upgrade
from reemote.facts.dnf.get_packages import Get_packages


class Upgrade(Operation_upgrade):
    """
    A class to manage package operations on a remote system using `dnf`.
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

    def upgrade_packages(self, packages=None,guard=None,present=None,sudo=None,su=None):
        return Upgrade(self.packages, self.guard and self.present, self.sudo, self.su)
