from reemote.command import Command
from reemote.operation import Operation

class Remove(Command):
    """
    Represents a removal operation for packages using yum.

    This class extends BaseRemove to provide specific functionality for the yum package manager.
    """
    def execute(self):
        yield Operation(f"yum remove -y {self.op}", guard=self.guard, sudo=self.sudo, su=self.su)