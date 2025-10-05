from reemote.command import (Command)
from reemote.operation import Operation

class Install(Command):
    """
    Represents an installation operation for packages using dpkg.

    This class extends BaseInstall to provide specific functionality for the dpkg package manager.
    """
    def execute(self):
        yield Operation(f"dpkg -i {self.op}", guard=self.guard, sudo=self.sudo, su=self.su)