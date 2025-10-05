from reemote.commands.base.install import Install
from reemote.operation import Operation

class Install(Install):
    """
    Represents an installation operation for packages using pipx.

    This class extends BaseInstall to provide specific functionality for the pipx package manager.
    """
    def execute(self):
        yield Operation(f"pipx install {self.op}", guard=self.guard, sudo=self.sudo, su=self.su)