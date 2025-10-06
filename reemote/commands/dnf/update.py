from reemote.update import Update
from reemote.operation import Operation
class Update(Update):
    """
    Represents an update command execution.

    This class is responsible for executing an update command using the underlying
    operation mechanism. It generates an operation that performs an "apk update"
    command with optional guards and permissions.
    """
    def execute(self):
        yield Operation(f"dnf update", guard=self.guard, sudo=self.sudo, su=self.su)
