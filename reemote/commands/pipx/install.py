from reemote.install import Command
from reemote.operation import Operation

class Install(Command):
    """
    Represents an installation operation for packages using pipx.

    This class extends BaseInstall to provide specific functionality for the pipx package manager.
    """
    def execute(self):
        yield Operation(f"pipx install {self.op}", guard=self.guard, sudo=self.sudo, su=self.su)