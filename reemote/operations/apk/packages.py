# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from typing import List
from reemote.operation_packages import Operation_packages
from reemote.commands.apk.install import Install
from reemote.commands.apk.remove import Remove
from reemote.facts.apk.get_packages import Get_packages


class Packages(Operation_packages):
    """
    A class to manage package operations on a remote system using `apk`.

    Description
    -----------
    This class extends the `Operation_packages` base class to provide functionality for managing packages
    on a remote system via the `apk` package manager. It supports installing, removing, and retrieving
    package information while allowing fine-grained control over execution privileges (`sudo`, `su`) and
    operational guards.

    Parameters
    ----------
    packages : List[str]
        A list of package names to be managed.
    present : bool
        Indicates whether the packages should be present (`True`) or absent (`False`) on the system.
    guard : bool, optional
        Enables or disables guard conditions for operations. Defaults to `True`.
    sudo : bool, optional
        Executes commands with `sudo` privileges if `True`. Defaults to `False`.
    su : bool, optional
        Executes commands with `su` privileges if `True`. Defaults to `False`.

    Methods
    -------
    get_packages()
        Retrieves the list of installed packages on the remote system.

        Returns
        -------
        Get_packages
            An instance of `Get_packages` containing the package information.

    install_packages(packages=None, guard=None, present=None, sudo=None, su=None)
        Installs the specified packages on the remote system.

        Parameters
        ----------
        packages : List[str], optional
            Overrides the default package list for this operation. Defaults to `None`.
        guard : bool, optional
            Overrides the default guard condition. Defaults to `None`.
        present : bool, optional
            Overrides the default presence condition. Defaults to `None`.
        sudo : bool, optional
            Overrides the default `sudo` setting. Defaults to `None`.
        su : bool, optional
            Overrides the default `su` setting. Defaults to `None`.

        Returns
        -------
        Install
            An instance of `Install` configured with the specified parameters.

    remove_packages(packages=None, guard=None, present=None, sudo=None, su=None)
        Removes the specified packages from the remote system.

        Parameters
        ----------
        packages : List[str], optional
            Overrides the default package list for this operation. Defaults to `None`.
        guard : bool, optional
            Overrides the default guard condition. Defaults to `None`.
        present : bool, optional
            Overrides the default presence condition. Defaults to `None`.
        sudo : bool, optional
            Overrides the default `sudo` setting. Defaults to `None`.
        su : bool, optional
            Overrides the default `su` setting. Defaults to `None`.

        Returns
        -------
        Remove
            An instance of `Remove` configured with the specified parameters.
    """

    def __init__(self,
                 packages: List[str],
                 present: bool,
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):
        print("trace 01")
        super().__init__(packages, present, guard, sudo, su)
        print("trace 02")

    def get_packages(self):
        print("trace 00")
        return Get_packages()

    def install_packages(self, packages=None, guard=None, present=None, sudo=None, su=None):
        return Install(self.packages, self.guard and self.present, self.sudo, self.su)

    def remove_packages(self, packages=None, guard=None, present=None, sudo=None, su=None):
        print("trace 20")
        return Remove(self.packages, self.guard and not self.present, self.sudo, self.su)