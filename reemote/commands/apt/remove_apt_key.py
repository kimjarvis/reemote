# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from typing import List
from reemote.command import Command

class Remove_apt_key:
    """
    Handles removing an apt GPG key from the system.

    This class is responsible for removing a GPG key from the system's
    apt keyring. It supports additional options such as using
    elevated privileges with `sudo` or `su`.

    Attributes:
        name (str): A descriptive name for the key being removed, used to
            identify the keyring builtin.
        guard (bool): Whether to add an execution guard for the
            operation. Defaults to True.
        sudo (bool): Indicates if the operation should be executed
            with `sudo` privileges. Defaults to True (typically required).
        su (bool): Indicates if the operation should be executed
            with `su` privileges. Defaults to False.
    """

    def __init__(self,
                 name: str = "",
                 guard: bool = True,
                 sudo: bool = True,
                 su: bool = False):
        self.name: str = name
        self.guard: bool = guard
        self.sudo: bool = sudo
        self.su: bool = su

    def __repr__(self) -> str:
        return (f"Remove_apt_key("
                f"name={self.name!r}, "
                f"guard={self.guard!r}, "                                
                f"sudo={self.sudo!r}, "
                f"su={self.su!r})")

    def execute(self):
        keyring_file = f"/usr/share/keyrings/{self.name.lower().replace(' ', '-')}.gpg"
        yield Command(
            f"rm -f {keyring_file}",
            guard=self.guard,
            sudo=self.sudo,
            su=self.su
        )
