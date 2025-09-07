from reemote.printers import print_ssh_completed_process
from typing import List

class Upgrade:
    """
    A class to manage package operations on a remote system using `apk` (Alpine Linux package manager).

    Attributes:
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
        su (bool): If `True`, the commands will be executed with `su` privileges.

    Usage:
        Upgrade installed packages.

    Notes:
        - Commands are constructed based on the `present`, `sudo`, and `su` flags.
        - The `changed` flag is set if the package state changes after execution.
    """

    def __init__(self,
                 sudo: bool = False,
                 su: bool = False):
        self.sudo: bool = sudo
        self.su: bool = su

    def __repr__(self) -> str:
        return (f"Upgrade("
                f"sudo={self.sudo!r}, su={self.su!r})")

    def execute(self):
        r0 = yield f"echo {self}"
        _sudo: str = "sudo -S " if self.sudo else ""
        _su: str = "su -c " if self.su else ""

        # Retrieve the current list of installed packages
        r1 = yield f"{_sudo}apk info -v"

        r2 = yield f"{_sudo}{_su}'apk upgrade'"

        # Retrieve the upgraded list of installed packages
        r3 = yield f"{_sudo}apk info -v"

        # Set the `changed` flag if the package state has changed
        if r1.cp.stdout != r3.cp.stdout:
            r2.changed = True