from typing import List
from reemote.operation import Operation
class Info:
    """
    A class to manage package operations on a remote system using `apk` (Alpine Linux package manager).

    Attributes:
        package (str): A package name to be queried.
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
        su (bool): If `True`, the commands will be executed with `su` privileges.

    **Examples:**

    .. code:: python

        yield Info(package='vim')

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Commands are constructed based on the `present`, `sudo`, and `su` flags.
    """

    def __init__(self,
                 package: str,
                 sudo: bool = False,
                 su: bool = False):
        self.package: str = package
        self.sudo: bool = sudo
        self.su: bool = su

    def __repr__(self) -> str:
        return (f"Info(package={self.package!r}, "
                f"sudo={self.sudo!r}, su={self.su!r})")

    def execute(self):
        r0 = yield Operation(f"{self}",composite=True)
        r0.executed = True

        # Retrieve the package information
        r1 = yield Operation(f"apk info {self.package}", sudo=self.sudo, su=self.su)
        # print(r1.cp.stdout)
