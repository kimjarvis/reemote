from reemote.operation import Operation
from reemote.upgrade import Upgrade
class Upgrade(Upgrade):
    """
    Represents a DNF upgrade command execution.

    This class is responsible for executing a system upgrade command using the
    DNF package manager (used in Fedora, RHEL 8+, and CentOS 8+). It generates
    an operation that performs a "dnf upgrade" command with optional guards and
    permission elevation.

    The class inherits from the base Upgrade class and provides DNF-specific
    implementation for upgrading installed packages to their latest available
    versions.

    Attributes:
        guard (bool): Inherited from parent class. Specifies whether to enable
                     guard to monitor changes during the upgrade process.
        sudo (bool): Inherited from parent class. Flag to indicate if the upgrade
                    should be executed with superuser privileges using 'sudo'.
        su (bool): Inherited from parent class. Flag to indicate if the upgrade
                  should be executed with a user switch using 'su'.

    Notes:
        - This class uses the DNF package manager command "dnf upgrade".
        - The operation supports guard conditions to track system changes.
        - Permission elevation can be configured via sudo or su flags.
    """
    def execute(self):
        yield Operation(f"dnf upgrade", guard=self.guard, sudo=self.sudo, su=self.su)