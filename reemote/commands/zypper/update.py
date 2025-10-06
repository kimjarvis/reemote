from reemote.update import Update
from reemote.operation import Operation
class Update(Update):
    """
    Implements package index update using the zypper package manager.

    This class extends Command to execute the `zypper update` command for updating package indexes.

    Attributes:
        guard: A boolean flag indicating whether the operation should be guarded.
        sudo: A boolean flag to specify if sudo privileges are required.
        su: A boolean flag to specify if the operation should run as su.

    **Examples:**

    .. code:: python

        yield Update()

    """
    def execute(self):
        yield Operation(f"zypper update", guard=self.guard, sudo=self.sudo, su=self.su)
