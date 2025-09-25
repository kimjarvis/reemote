from typing import List
from reemote.operation import Operation

class Packages:
    """
    A class to manage package operations on a remote system using `pipx`.

    Attributes:
        packages (List[str]): A list of package names to be added or removed.
        present (bool): Indicates whether the packages should be present (`True`) or absent (`False`) on the system.
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
        su (bool): If `True`, the commands will be executed with `su` privileges.

    **Examples:**

    .. code:: python

        # Add the packages on all hosts
        r = yield Packages(packages=["vim","asterisk"],present=True, su=True)
        # Verify installation
        r = yield Shell("which vim")
        print(r.cp.stdout)
        # Delete the packages on all hosts
        r = yield Packages(packages=["vim","asterisk"],present=False, su=True)
        # Verify removal
        r = yield Shell("which vim")
        print(r.cp.stdout)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.
        It supports adding or removing packages based on the `present` flag and allows privilege escalation via `sudo` or `su`.

    Notes:
        - Commands are constructed based on the `present`, `sudo`, and `su` flags.
        - The `changed` flag is set if the package state changes after execution.
    """

    def __init__(self,
                 packages: List[str],
                 present: bool,
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):
        self.packages: List[str] = packages
        self.present: bool = present
        self.guard: bool = guard
        self.sudo: bool = sudo
        self.su: bool = su

        # Construct the operation string from the list of packages
        op: List[str] = []
        op.extend(self.packages)
        self.op: str = " ".join(op)

    def __repr__(self) -> str:
        return (f"Packages(packages={self.packages!r}, present={self.present!r},"
                f"guard={self.guard!r}, "                                
                f"sudo={self.sudo!r}, su={self.su!r})")

    def execute(self):
        r0 = yield Operation(f"{self}",composite=True)
        r0.executed = self.guard

        # Retrieve the current list of installed packages
        r1 = yield Operation(f"pipx list",guard=self.guard, sudo=self.sudo, su=self.su)

        # Add or remove packages based on the `present` flag
        r2 = yield Operation(f"pipx install {self.op}",guard=self.guard and self.present, sudo=self.sudo, su=self.su)

        r3 = yield Operation(f"pipx uninstall {self.op}",guard=self.guard and not self.present, sudo=self.sudo, su=self.su)

        # Retrieve the updated list of installed packages
        r4 = yield Operation(f"pipx list",guard=self.guard, sudo=self.sudo, su=self.su)

        # Set the `changed` flag iff the package state has changed
        if self.guard and (r1.cp.stdout != r4.cp.stdout):
            r2.changed = self.guard and self.present
            r3.changed = self.guard and not self.present
            r0.changed = True
