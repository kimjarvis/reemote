
from reemote.command import Command
from reemote.operation import Operation

class Install(Command):
    """
    Implements package installation using the apt package manager.

    This class extends Command to execute the `apt install -y` command for installing packages.

    Attributes:
        packages: List of package names to be installed.
        guard: A boolean flag indicating whether the operation should be guarded.
        sudo: A boolean flag to specify if sudo privileges are required.
        su: A boolean flag to specify if the operation should run as su.

    **Examples:**

    .. code:: python

        yield Install(packages=['vim', 'git'])

    """
    def execute(self):
        yield Operation(f"apt install -y {self.op}", guard=self.guard, sudo=self.sudo, su=self.su)