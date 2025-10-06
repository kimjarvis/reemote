from reemote.operation import Operation
from reemote.upgrade import Upgrade
class Upgrade(Upgrade):
    """
    Represents an upgrade command execution.

    This class is responsible for executing an update command using the underlying
    operation mechanism. It generates an operation that performs an "apk update"
    command with optional guards and permissions.
    """
    def execute(self):
        yield Operation(f"apk upgrade", guard=self.guard, sudo=self.sudo, su=self.su)
