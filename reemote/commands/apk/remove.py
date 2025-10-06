from reemote.command import Command
from reemote.operation import Operation

class Remove(Command):
    """
    Implements package removal using the apk package manager.

    This class extends Command to execute the `apk del` command for removing packages.

    Attributes:
        packages: List of package names to be removed.
        guard: A boolean flag indicating whether the operation should be guarded.
        sudo: A boolean flag to specify if sudo privileges are required.
        su: A boolean flag to specify if the operation should run as su.

    **Examples:**

    .. code:: python

        yield Remove(packages=['vim', 'git'])

    """
    def execute(self):
        yield Operation(f"apk del {self.op}", guard=self.guard, sudo=self.sudo, su=self.su)