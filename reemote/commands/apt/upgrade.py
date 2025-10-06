from reemote.operation import Operation
from reemote.upgrade import Upgrade
class Upgrade(Upgrade):
    """
    Represents a system upgrade operation.

    This class encapsulates the logic to perform a system upgrade by yielding
    an operation that interacts with the system. The upgrade is executed using
    the `apt-get upgrade` command. It supports features like guard conditions,
    and using sudo or su for permissions.
    """
    def execute(self):
        yield Operation(f"apt-get upgrade", guard=self.guard, sudo=self.sudo, su=self.su)
