from reemote.commands.base.install import Install
from reemote.operation import Operation

class Remove(Install):
    """
    Represents a removal operation for packages using zypper.

    This class extends BaseRemove to provide specific functionality for the zypper package manager.
    """
    def execute(self):
        yield Operation(f"zypper remove -y {self.op}", guard=self.guard, sudo=self.sudo, su=self.su)