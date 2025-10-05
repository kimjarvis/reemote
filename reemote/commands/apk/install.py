from reemote.command import Command
from reemote.operation import Operation
class Install(Command):
    """
    Represents an installation operation for packages using apk.

    This class extends BaseInstall to provide specific functionality for the apk package manager.
    """
    def execute(self):
        yield Operation(f"apk add {self.op}", guard=self.guard, sudo=self.sudo, su=self.su)
