from reemote.command import Command
from reemote.operation import Operation

class Remove(Command):
    """
    Represents a removal operation for packages using dnf.

    This class extends BaseRemove to provide specific functionality for the dnf package manager.
    """
    def execute(self):
        yield Operation(f"dnf remove -y {self.op}", guard=self.guard, sudo=self.sudo, su=self.su)