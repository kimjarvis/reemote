from reemote.update import Update
from reemote.operation import Operation
class Update(Update):
    """
    Representation of an update operation.

    This class defines an update operation that uses an external package manager
    to refresh available packages and updates the system's package list.
    """
    def execute(self):
        yield Operation(f"apt-get update", guard=self.guard, sudo=self.sudo, su=self.su)
