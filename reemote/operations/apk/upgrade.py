from reemote.operation import Operation
from reemote.operation import Operation


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
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):
        self.guard = guard
        self.sudo: bool = sudo
        self.su: bool = su

    def __repr__(self) -> str:
        return (f"Upgrade("
                f"guard={self.guard!r}, "                                
                f"sudo={self.sudo!r}, su={self.su!r})")

    def execute(self):
        r0 = yield Operation(f"composite {self}", guard=self.guard)
        r0.executed = self.guard
        _sudo: str = "sudo -S " if self.sudo else ""
        _su: str = "su -c " if self.su else ""

        # Retrieve the current list of installed packages
        r1 = yield Operation(f"{_sudo}apk info -v", guard=self.guard)

        r2 = yield Operation(f"{_sudo}{_su}'apk upgrade'", guard=self.guard)

        # Retrieve the upgraded list of installed packages
        r3 = yield Operation(f"{_sudo}apk info -v", guard=self.guard)

        # Set the `changed` flag if the package state has changed
        if self.guard and (r1.cp.stdout != r3.cp.stdout):
            r2.changed = True
            r0.changed = True