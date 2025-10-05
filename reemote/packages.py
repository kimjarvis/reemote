from typing import List
from reemote.operation import Operation


class Packages:
    """
    Encapsulates the management of software package installation and removal.

    This class provides functionality to ensure that specified software
    packages are either installed or removed according to a given state. It
    can execute operations, track changes in the package states, and control
    how actions are performed using various flags like repository and superuser
    privileges.

    Attributes:
        packages (List[str]): The list of package names to manage.
        present (bool): Indicates whether the packages should be installed
            (True) or removed (False).
        guard (bool): Determines whether to execute the associated operations.
        sudo (bool): If True, executes actions with sudo privileges.
        su (bool): If True, executes actions with su privileges.
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

    def __repr__(self) -> str:
        return (f"Packages(packages={self.packages!r}, "
                f"present={self.present!r},"
                f"guard={self.guard!r}, "                                
                f"sudo={self.sudo!r}, "
                f"su={self.su!r})")

    def execute(self):
        # Start a composite operation
        r0 = yield Operation(f"{self}", composite=True)
        r0.executed = self.guard

        # Retrieve the current list of installed packages
        r1 = yield self.get_packages()

        # Add or remove packages based on the `present` flag
        if self.present:
            r2 = yield self.install_packages()
        else:
            r2 = yield self.remove_packages()

        # Retrieve the updated list of installed packages
        r4 = yield self.get_packages()

        # Set the `changed` flag if the package state has changed
        if self.guard and (r1.cp.stdout != r4.cp.stdout):
            r2.changed = self.guard and self.present
            r0.changed = True

    def get_packages(self):
        """
        Retrieve the list of installed packages.
        Subclasses must implement this method.
        """
        raise NotImplementedError("Subclasses must implement `get_packages`.")

    def install_packages(self):
        """
        Install the specified packages.
        Subclasses must implement this method.
        """
        raise NotImplementedError("Subclasses must implement `install_packages`.")

    def remove_packages(self):
        """
        Remove the specified packages.
        Subclasses must implement this method.
        """
        raise NotImplementedError("Subclasses must implement `remove_packages`.")