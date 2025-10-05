from typing import List
from reemote.operation import Operation
class Remove:
    """
    Represents a class to remove specified packages.

    This class facilitates the removal of specified packages through a generated
    command string. It provides flexibility to include optional execution features
    like guarding the action and using elevated permissions such as sudo or su.

    Attributes:
        packages (list of str): List of package names to be removed.
        guard (bool): Whether to guard the remove operation. Defaults to True.
        sudo (bool): Whether to execute the remove operation with sudo permissions.
                     Defaults to False.
        su (bool): Whether to execute the remove operation with su permissions.
                   Defaults to False.
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

        # Ensure self.packages is always a list of strings
        self.packages: List[str] = [packages] if isinstance(packages, str) else packages

        # Construct the operation string from the list of packages
        op: List[str] = []
        op.extend(self.packages)
        self.op: str = " ".join(op)

    def __repr__(self) -> str:
        return (f"Remove("
                f"packages={self.packages!r}, "
                f"guard={self.guard!r}, "                                
                f"sudo={self.sudo!r}, "
                f"su={self.su!r})")

    def execute(self):
        yield Operation(f"pip uninstall {self.op}",guard=self.guard, sudo=self.sudo, su=self.su)

