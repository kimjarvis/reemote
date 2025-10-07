from typing import List
from reemote.operation_update import Operation_update
from reemote.commands.apk.update import Update
from reemote.facts.apk.get_packages import Get_packages


class Update(Operation_update):
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

    def update(self, guard=None,sudo=None,su=None):
        from reemote.commands.apk.update import Update
        return Update(self.guard, self.sudo, self.su)
