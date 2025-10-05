from typing import List
from reemote.operation import Operation

class Install:
    """
    Represents an installation operation for packages using apt-get.

    This class is designed to manage package installations with options for adding
    guards, sudo privileges, or the su user. It constructs the operation string
    from the provided packages and represents an encapsulated way of handling
    package installation commands.

    Attributes:
        packages: List of package names to be installed.
        guard: A boolean flag indicating whether the operation should be guarded.
        sudo: A boolean flag to specify if sudo privileges are required.
        su: A boolean flag to specify if the operation should run as su.

    """

    def __init__(self,
                 packages: List[str],
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):

        self.packages: List[str] = packages
        self.guard: bool = guard
        self.sudo: bool = sudo
        self.su: bool = su

        # Construct the operation string from the list of packages
        op: List[str] = []
        op.extend(self.packages)
        self.op: str = " ".join(op)

    def __repr__(self) -> str:
        return (f"Install("
                f"packages={self.packages!r}, "
                f"guard={self.guard!r}, "                                
                f"sudo={self.sudo!r}, "
                f"su={self.su!r})")

    def execute(self):
        yield Operation(f"apt-get install -y {self.op}",guard=self.guard, sudo=self.sudo, su=self.su)

