from reemote.command import Command
from reemote.operation import Operation

class Install(Command):
    """
    Represents an installation operation for packages using apt.

    This class extends BaseInstall to provide specific functionality for the apt package manager.
    """
    def execute(self):
        yield Operation(f"apt install -y {self.op}", guard=self.guard, sudo=self.sudo, su=self.su)