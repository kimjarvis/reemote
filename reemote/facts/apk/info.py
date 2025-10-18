# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from typing import List
from reemote.command import Command
class Info:
    """A class to manage package operations on a remote system using `apk`.

    This class constructs commands to retrieve information about a specific
    package on an Alpine Linux system.

    Attributes:
        package (str): A package name to be queried.
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
        su (bool): If `True`, the commands will be executed with `su` privileges.

    **Examples:**

    .. code:: python

        yield Info(package='vim')

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution.

    Notes:
        - Commands are constructed based on the `package`, `sudo`, and `su` attributes.
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
        r0 = yield Command(f"{self}", composite=True)
        r0.executed = True

        # Retrieve the package information
        r1 = yield Command(f"apk info {self.package}", sudo=self.sudo, su=self.su)
        # print(r1.cp.stdout)
