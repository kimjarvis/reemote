from reemote.install import Command
from reemote.operation import Operation

class Install(Command):
    """
    Represents an installation operation for packages using zypper.

    This class extends BaseInstall to provide specific functionality for the zypper package manager.
    """
    def execute(self):
        yield Operation(f"zypper install -y {self.op}", guard=self.guard, sudo=self.sudo, su=self.su)