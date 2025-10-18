# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from typing import List
from reemote.command import Command

class Add_apt_key:
    """
    Handles adding an apt GPG key to the system.

    This class is responsible for downloading and adding a GPG key
    for apt repositories. It supports additional options such as using
    elevated privileges with `sudo` or `su`.

    Attributes:
        source (str): The URL source of the GPG key.
        guard (bool): Whether to add an execution guard for the
            operation. Defaults to True.
        sudo (bool): Indicates if the operation should be executed
            with `sudo` privileges. Defaults to True (typically required).
        su (bool): Indicates if the operation should be executed
            with `su` privileges. Defaults to False.
    """

    def __init__(self,
                 comment: str = "",
                 source: str = "",
                 guard: bool = True,
                 sudo: bool = True,
                 su: bool = False):
        self.comment: str = comment
        self.source: str = source
        self.guard: bool = guard
        self.sudo: bool = sudo
        self.su: bool = su

    def __repr__(self) -> str:
        return (f"Add_apt_key("
                f"comment={self.comment!r}, "
                f"source={self.source!r}, "
                f"guard={self.guard!r}, "                                
                f"sudo={self.sudo!r}, "
                f"su={self.su!r})")

    def execute(self):
        yield Command(
            f"curl -fsSL {self.source} | gpg --dearmor -o /usr/share/keyrings/{self.comment.lower().replace(' ', '-')}.gpg",
            guard=self.guard,
            sudo=self.sudo,
            su=self.su
        )
