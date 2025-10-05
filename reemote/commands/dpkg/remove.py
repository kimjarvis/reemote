from reemote.install import Command
from reemote.operation import Operation

class Remove(Command):
    """
    Represents a removal operation for packages using dpkg.

    This class extends BaseRemove to provide specific functionality for the dpkg package manager.
    """
    def execute(self):
        yield Operation(f"dpkg -r {self.op}", guard=self.guard, sudo=self.sudo, su=self.su)