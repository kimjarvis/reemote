# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from typing import List
from reemote.command import Command

class Remove_ppa:
    """
    Represents a class for removing a Personal Package Archive (PPA) from the system's
    package manager.

    This class encapsulates information and actions required to remove a PPA from the
    system using specific configurations such as enabling guard checks, using sudo, and
    switching to a superuser.

    Attributes:
        ppa: The PPA repository identifier to be removed.
        guard: A boolean flag to enable or disable guard checks during the operation.
        sudo: A boolean flag indicating whether the operation should be executed
              with sudo privileges.
        su: A boolean flag indicating whether the operation should be executed as
            a superuser.
    """

    def __init__(self,
                 ppa: str = "",
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):
        self.ppa: str = ppa
        self.guard: bool = guard
        self.sudo: bool = sudo
        self.su: bool = su

    def __repr__(self) -> str:
        return (f"Remove_ppa("
                f"ppa={self.ppa!r}, "
                f"guard={self.guard!r}, "                                
                f"sudo={self.sudo!r}, "
                f"su={self.su!r})")

    def execute(self):
        yield Command(f"apt-add-repository -y remove {self.ppa}", guard=self.guard, sudo=self.sudo, su=self.su)

