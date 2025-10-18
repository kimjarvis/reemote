# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from typing import List
from reemote.command import Command
from reemote.utilities.validate_list_of_strings import validate_list_of_strings
class Command_update:
    """
    Represents an installation or removal operation for packages.

    This class is designed to manage package installations with options for adding
    guards, sudo privileges, or the su user. It constructs the operation string
    from the provided packages and represents an encapsulated way of handling
    package installation commands.

    Attributes:
        packages: List of package names to be installed.
        guard: A boolean flag indicating whether the operation should be guarded.
        sudo: A boolean flag to specify if sudo privileges are required.
        su: A boolean flag to specify if the operation should run as su.

    """
    def __init__(self,
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):

        self.guard: bool = guard
        self.sudo: bool = sudo
        self.su: bool = su

    def __repr__(self) -> str:
        return (f"Command("
                f"guard={self.guard!r}, "                                
                f"sudo={self.sudo!r}, "
                f"su={self.su!r})")


