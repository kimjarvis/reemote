# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class Hostname:
    """
    A class to manage the system's hostname on Unix-like operating systems.
    This class allows setting the hostname and supports different strategies
    for updating the hostname based on the underlying OS/distribution.

    Attributes:
        name (str): The desired hostname to set.
        use (str): Strategy to use for updating hostname.
        guard (bool): If `False` the commands will not be executed.

    Supported strategies:
        - "alpine"
        - "debian"
        - "freebsd"
        - "generic"
        - "macos" / "macosx" / "darwin"
        - "openbsd"
        - "openrc"
        - "redhat"
        - "sles"
        - "solaris"
        - "systemd"

    **Examples:**

    .. code:: python

        # Set a hostname
        r = yield Hostname(name="web01")
        print(r.cp.stdout)

        # Set a hostname specifying strategy
        r = yield Hostname(name="web01", use="systemd")
        print(r.cp.stdout)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - This module does NOT modify /etc/hosts. You need to modify it yourself using other modules.
        - On macOS, this module uses scutil to set HostName, ComputerName, and LocalHostName.
        - Windows, HP-UX, and AIX are not currently supported.
    """

    def __init__(self,
                 name: str,
                 use: str = None,
                 guard: bool = True):

        self.name = name
        self.use = use
        self.guard = guard

    def __repr__(self):
        return (f"Hostname(name={self.name!r}, "
                f"use={self.use!r}, "
                f"guard={self.guard!r})")

    def execute(self):
        # Build the command based on the strategy
        if self.use == "systemd":
            cmd = f"hostnamectl set-hostname {self.name}"
        elif self.use == "redhat":
            cmd = f"echo '{self.name}' > /etc/hostname && hostname {self.name}"
        elif self.use == "debian":
            cmd = f"echo '{self.name}' > /etc/hostname && hostname --file /etc/hostname"
        elif self.use == "macos" or self.use == "macosx" or self.use == "darwin":
            # Replace special characters for LocalHostName
            local_name = ''.join(c if c.isalnum() or c in ['-', '.'] else '-' for c in self.name)
            cmd = (f"scutil --set HostName {self.name} && "
                   f"scutil --set ComputerName {self.name} && "
                   f"scutil --set LocalHostName {local_name}")
        elif self.use == "generic":
            cmd = f"hostname {self.name}"
        else:
            # Auto-detect or fallback to generic approach
            cmd = f"hostnamectl set-hostname {self.name} || hostname {self.name}"

        r = yield Command(cmd, guard=self.guard)
        r.changed = True