from reemote.command import Command
from reemote.operation import Operation

class Install(Command):
    """
    Represents an installation operation for packages using pacman.

    This class extends BaseInstall to provide specific functionality for the pacman package manager.
    """
    def execute(self):
        yield Operation(f"pacman --noconfirm -S {self.op}", guard=self.guard, sudo=self.sudo, su=self.su)