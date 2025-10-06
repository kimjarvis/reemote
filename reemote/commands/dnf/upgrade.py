from reemote.command import Command
from reemote.operation import Operation

class Upgrade(Command):
    """
    Implements package upgrade using the dnf package manager.

    This class extends Command to execute the `dnf upgrade -y` command for upgrading installed packages.

    Attributes:
        guard: A boolean flag indicating whether the operation should be guarded.
        sudo: A boolean flag to specify if sudo privileges are required.
        su: A boolean flag to specify if the operation should run as su.

    **Examples:**

    .. code:: python

        yield Upgrade()

    """
    def execute(self):
        yield Operation(f"dnf upgrade -y", guard=self.guard, sudo=self.sudo, su=self.su)