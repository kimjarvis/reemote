from reemote.command import Command
from reemote.operation import Operation

class Upgrade(Command):
    """
    Implements package upgrade using the apt package manager.

    This class extends Command to execute the `apt upgrade -y` command for upgrading installed packages.

    Attributes:
        packages: List of package names (typically empty for upgrade operations).
        guard: A boolean flag indicating whether the operation should be guarded.
        sudo: A boolean flag to specify if sudo privileges are required.
        su: A boolean flag to specify if the operation should run as su.

    **Examples:**

    .. code:: python

        yield Upgrade()

    """
    def execute(self):
        yield Operation(f"apt upgrade -y", guard=self.guard, sudo=self.sudo, su=self.su)