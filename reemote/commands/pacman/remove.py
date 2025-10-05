from reemote.install import Command
from reemote.operation import Operation

class Remove(Command):
    """
    Represents a removal operation for packages using pacman.

    This class extends BaseRemove to provide specific functionality for the pacman package manager.
    """
    def execute(self):
        yield Operation(f"pacman --noconfirm -R {self.op}", guard=self.guard, sudo=self.sudo, su=self.su)