# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from typing import List
from reemote.command import Command

class Add_ppa:
    """
    Handles adding a Personal Package Archive (PPA) to the system.

    This class is responsible for configuring a PPA repository on
    a system. It supports additional options such as using
    elevated privileges with `sudo` or `su`. It also allows for a
    safety guard to control the execution of the repository addition.

    Attributes:
        ppa (str): The PPA repository string to be added.
        guard (bool): Whether to add an execution guard for the
            operation. Defaults to True.
        sudo (bool): Indicates if the operation should be executed
            with `sudo` privileges. Defaults to False.
        su (bool): Indicates if the operation should be executed
            with `su` privileges. Defaults to False.
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
        return (f"Add_ppa("
                f"ppa={self.ppa!r}, "
                f"guard={self.guard!r}, "                                
                f"sudo={self.sudo!r}, "
                f"su={self.su!r})")

    def execute(self):
        yield Command(f"apt-add-repository -y {self.ppa}", guard=self.guard, sudo=self.sudo, su=self.su)

