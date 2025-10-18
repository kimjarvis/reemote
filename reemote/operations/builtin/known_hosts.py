# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class KnownHosts:
    """
    A class to encapsulate the functionality of managing SSH known_hosts entries.
    It allows users to add or remove host keys from the known_hosts file.

    Attributes:
        name (str): The host to add or remove (must match a host specified in key).
        key (str): The SSH public host key, as a string.
        path (str): The known_hosts file to edit.
        state (str): Whether to add (present) or remove (absent) host keys.
        hash_host (bool): Hash the hostname in the known_hosts file.

    **Examples:**

    .. code:: python

        # Add a host key to the system known_hosts file
        r = yield KnownHosts(
            name="foo.com.invalid",
            key="foo.com.invalid ssh-rsa AAAAB3NzaC1yc2E...",
            path="/etc/ssh/ssh_known_hosts"
        )

        # Remove a host key
        r = yield KnownHosts(
            name="oldserver.example.com",
            state="absent",
            path="/etc/ssh/ssh_known_hosts"
        )

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Starting at Ansible 2.2, multiple entries per host are allowed, but only one for each key type supported by ssh.
        - For custom SSH ports, both name and key need to specify the port (e.g., '[host.example.com]:2222').
        - The known_hosts file will be created if needed, but the rest of the path must exist.
    """

    def __init__(self,
                 name: str,
                 key: str = None,
                 path: str = "~/.ssh/known_hosts",
                 state: str = "present",
                 hash_host: bool = False):

        self.name = name
        self.key = key
        self.path = path
        self.state = state
        self.hash_host = hash_host

    def __repr__(self):
        return (f"KnownHosts(name={self.name!r}, "
                f"key={self.key!r}, "
                f"path={self.path!r}, "
                f"state={self.state!r}, "
                f"hash_host={self.hash_host!r})")

    def execute(self):
        # Validate required parameters
        if self.state == "present" and not self.key:
            raise ValueError("Parameter 'key' is required when state='present'")

        # Build the ansible command
        cmd_parts = ["ansible.builtin.known_hosts"]
        cmd_parts.append(f"name={self.name}")

        if self.key:
            cmd_parts.append(f"key='{self.key}'")
        if self.path:
            cmd_parts.append(f"path={self.path}")
        if self.state:
            cmd_parts.append(f"state={self.state}")
        if self.hash_host:
            cmd_parts.append("hash_host=yes")
        else:
            cmd_parts.append("hash_host=no")

        ansible_cmd = " ".join(cmd_parts)

        # Execute via Command
        r = yield Command(ansible_cmd, guard=True, sudo=True)
        r.changed = True