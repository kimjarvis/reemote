# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class AddHost:
    """
    A class to encapsulate the functionality of adding hosts to the in-memory inventory
    similar to Ansible's ansible.builtin.add_host module.

    This allows dynamic addition of hosts and groups to the inventory during playbook execution,
    making them available for use in subsequent plays.

    Attributes:
        name (str): The hostname/ip of the host to add to the inventory, can include a colon and a port number.
        groups (list): The groups to add the hostname to.
        **kwargs: Additional variables to assign to the host.

    **Examples:**

    .. code:: python

        # Add a host to a group with custom variables
        yield AddHost(name='192.168.1.100', groups=['web_servers'], foo=42)

        # Add a host to multiple groups
        yield AddHost(hostname='192.168.1.101', groups=['group1', 'group2'])

        # Add a host with non-standard port
        yield AddHost(name='192.168.1.102:2222')

        # Add a host with tunnel configuration
        yield AddHost(hostname='192.168.1.103', ansible_host='localhost', ansible_port=2222)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Hosts added will not bypass the --limit from the command line
        - The host is available from hostvars and for delegation as a normal part of the inventory
        - Changed status is provided as it can be useful for tracking inventory changes
    """

    def __init__(self,
                 name: str = None,
                 hostname: str = None,
                 groups=None,
                 group=None,
                 groupname=None,
                 **kwargs):

        # Handle aliases for name parameter
        if name is None and hostname is not None:
            self.name = hostname
        elif name is None and hostname is None:
            raise ValueError("Either 'name' or 'hostname' must be specified")
        else:
            self.name = name

        # Handle aliases for groups parameter
        if groups is None and group is not None:
            self.groups = [group] if isinstance(group, str) else group
        elif groups is None and groupname is not None:
            self.groups = [groupname] if isinstance(groupname, str) else groupname
        elif groups is None:
            self.groups = []
        else:
            self.groups = groups if isinstance(groups, list) else [groups]

        # Store additional variables as attributes
        self.variables = kwargs

    def __repr__(self):
        groups_repr = repr(self.groups)
        vars_repr = ', '.join([f"{k}={v!r}" for k, v in self.variables.items()])
        return (f"AddHost(name={self.name!r}, "
                f"groups={groups_repr}, "
                f"{vars_repr})")

    def execute(self):
        # Create the command string that would represent this operation
        # In a real implementation, this would interact with the inventory system
        cmd_parts = [f"add_host name={self.name}"]

        if self.groups:
            groups_str = ','.join(self.groups)
            cmd_parts.append(f"groups={groups_str}")

        for key, value in self.variables.items():
            cmd_parts.append(f"{key}={value}")

        cmd = ' '.join(cmd_parts)

        # Yield a command object (placeholder for actual inventory manipulation)
        r = yield Command(cmd, guard=True, sudo=False, su=False)
        r.changed = True
        r.add_host_result = {
            'name': self.name,
            'groups': self.groups,
            'variables': self.variables
        }