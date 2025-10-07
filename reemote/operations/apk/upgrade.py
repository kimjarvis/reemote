from typing import List
from reemote.operation_upgrade import Operation_upgrade
from reemote.commands.apk.upgrade import Upgrade
from reemote.facts.apk.get_packages import Get_packages


class Upgrade(Operation_upgrade):
    """
    A class to manage package operations on a remote system using `apk`.
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
