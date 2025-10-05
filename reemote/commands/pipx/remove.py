from reemote.commands.base.install import Install
from reemote.operation import Operation

class Remove(Install):
    """
    Represents a removal operation for packages using pipx.

    This class extends BaseRemove to provide specific functionality for the pipx package manager.
    """
    def execute(self):
        yield Operation(f"pipx uninstall {self.op}", guard=self.guard, sudo=self.sudo, su=self.su)