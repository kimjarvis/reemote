from reemote.command import Command
from reemote.operation import Operation

class Remove(Command):
    """
    Implements package removal using the yum package manager.

    This class extends Command to execute the `yum remove -y` command for removing packages.

    Attributes:
        packages: List of package names to be removed.
        guard: A boolean flag indicating whether the operation should be guarded.
        sudo: A boolean flag to specify if sudo privileges are required.
        su: A boolean flag to specify if the operation should run as su.

    **Examples:**

    .. code:: python

        yield Remove(packages=['black', 'pytest'])

    """
    def execute(self):
        yield Operation(f"yum remove -y {self.op}", guard=self.guard, sudo=self.sudo, su=self.su)