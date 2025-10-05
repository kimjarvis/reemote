from reemote.command import Command
from reemote.operation import Operation

class Remove(Command):
    """
    Represents a removal operation for packages using pip.

    This class extends BaseRemove to provide specific functionality for the pip package manager.
    """
    def execute(self):
        yield Operation(f"pip uninstall -y {self.op}", guard=self.guard, sudo=self.sudo, su=self.su)