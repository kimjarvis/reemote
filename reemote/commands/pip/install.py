from reemote.commands.base.install import Install
from reemote.operation import Operation

class Install(Install):
    """
    Represents an installation operation for packages using pip.

    This class extends BaseInstall to provide specific functionality for the pip package manager.
    """
    def execute(self):
        yield Operation(f"pip install {self.op}", guard=self.guard, sudo=self.sudo, su=self.su)