# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
class Operation_upgrade:
    """
    Manages update operations under specific guard and privilege settings.

    This class is responsible for performing updates with options to
    guard the operation, run as superuser using 'sudo', or switch user
    using 'su'. It provides an interface for controlled execution of
    update commands.

    Attributes:
        guard: Specifies whether to enable guard to monitor changes during
               the update process (default is True).
        sudo: A flag to indicate if the update should be executed with
              superuser privileges using 'sudo' (default is False).
        su: A flag to indicate if the update should be executed with
            a user switch (default is False).

    Methods:
        execute:
            Executes the update process while optionally monitoring for
            changes and employing privilege settings depending on `guard`,
            `sudo`, and `su` attributes.
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
                f"sudo={self.sudo!r}, "
                f"su={self.su!r})")

    def execute(self):
        r1 = yield self.get_packages()
        r2 = yield self.upgrade()
        r3 = yield self.get_packages()
        # Set the `changed` flag if the package state has changed
        if self.guard and (r1.cp.stdout != r3.cp.stdout):
            r2.changed = True

    def get_packages(self):
        """
        Retrieve the list of installed packages.
        Subclasses must implement this method.
        """
        raise NotImplementedError("Subclasses must implement `get_packages`.")

    def upgrade(self,guard=None,sudo=None,su=None):
        """
        Install the specified packages.
        Subclasses must implement this method.
        """
        raise NotImplementedError("Subclasses must implement `upgrade`.")
