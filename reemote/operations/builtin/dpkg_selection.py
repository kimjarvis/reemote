# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class DpkgSelections:
    """
    A class to manage dpkg package selection states using `dpkg --get-selections` and `dpkg --set-selections`.

    This class allows setting the selection state of a Debian package without installing/removing it.
    It supports standard dpkg selection states such as 'install', 'hold', 'deinstall', and 'purge'.

    Attributes:
        name (str): The name of the package.
        selection (str): The desired selection state for the package.
                         Choices: 'install', 'hold', 'deinstall', 'purge'.
        guard (bool): If `False`, the command will not be executed.

    Examples:
        .. code:: python

            # Hold a package to prevent upgrades
            r = yield DpkgSelections(name="python", selection="hold")
            print(r.cp.stdout)

            # Allow a package to be upgraded again
            r = yield DpkgSelections(name="python", selection="install")
            print(r.cp.stdout)

    Notes:
        - This module only changes the selection state; it does not install or remove packages.
          Use the apt module for actual package management.
        - Only works on Debian-based systems where dpkg is available.
    """

    VALID_SELECTIONS = {"install", "hold", "deinstall", "purge"}

    def __init__(self,
                 name: str,
                 selection: str,
                 guard: bool = True):
        if selection not in self.VALID_SELECTIONS:
            raise ValueError(
                f"Invalid selection '{selection}'. Must be one of {self.VALID_SELECTIONS}."
            )

        self.name = name
        self.selection = selection
        self.guard = guard

    def __repr__(self):
        return (
            f"DpkgSelections(name={self.name!r}, "
            f"selection={self.selection!r}, "
            f"guard={self.guard!r})"
        )

    def execute(self):
        # Construct the command to set the dpkg selection
        cmd_str = f"echo '{self.name} {self.selection}' | dpkg --set-selections"
        r = yield Command(cmd_str, guard=self.guard)
        r.changed = True