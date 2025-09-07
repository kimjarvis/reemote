from reemote.printers import print_ssh_completed_process
from typing import List

class Packages:
    """
    A class to manage package operations on a remote system using `apk` (Alpine Linux package manager).

    Attributes:
        packages (List[str]): A list of package names to be added or removed.
        present (bool): Indicates whether the packages should be present (`True`) or absent (`False`) on the system.
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
        su (bool): If `True`, the commands will be executed with `su` privileges.

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
                 repository: str = None ,
                 sudo: bool = False,
                 su: bool = False):
        self.packages: List[str] = packages
        self.present: bool = present
        self.repository: str = repository
        self.sudo: bool = sudo
        self.su: bool = su

        # Construct the operation string from the list of packages
        op: List[str] = []
        op.extend(self.packages)
        if repository:
            op.append(f"--repository {repository}")
        self.op: str = " ".join(op)

    def __repr__(self) -> str:
        return (f"Packages(packages={self.packages!r}, present={self.present!r},"
                f"repository={self.repository!r},"
                f"sudo={self.sudo!r}, su={self.su!r})")

    def execute(self):
        r0 = yield f"echo {self}"
        _sudo: str = "sudo -S " if self.sudo else ""
        _su: str = "su -c " if self.su else ""

        # Retrieve the current list of installed packages
        r1 = yield f"{_sudo}apk info -v"

        # Add or remove packages based on the `present` flag
        if self.present:
            r2 = yield f"{_sudo}{_su}'apk add {self.op}'"
        else:
            r2 = yield f"{_sudo}{_su}'apk del {self.op}'"

        # Retrieve the updated list of installed packages
        r3 = yield f"{_sudo}apk info -v"

        # Set the `changed` flag if the package state has changed
        if r1.cp.stdout != r3.cp.stdout:
            r2.changed = True
