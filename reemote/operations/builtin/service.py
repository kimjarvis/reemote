# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class Service:
    """
    A class to manage services on remote hosts, similar to Ansible's ansible.builtin.service module.
    This class allows users to control services by specifying the service name, desired state,
    whether it should be enabled at boot, and other service-specific options.

    Attributes:
        name (str): Name of the service.
        state (str): Desired state of the service (started, stopped, restarted, reloaded).
        enabled (bool): Whether the service should start on boot.
        pattern (str): Pattern to look for in ps output if service doesn't respond to status.
        arguments (str): Additional arguments provided on the command line.
        runlevel (str): Runlevel for OpenRC init scripts (Gentoo).
        sleep (int): Seconds to sleep between stop and start when restarting.
        use (str): Force specific service module (systemd, sysvinit, etc.).
        guard (bool): If False, the commands will not be executed.

    **Examples:**

    .. code:: python

        # Start service httpd, if not started
        r = yield Service(name="httpd", state="started")
        print(r.cp.stdout)

        # Stop service httpd, if started
        r = yield Service(name="httpd", state="stopped")

        # Enable service httpd, and not touch the state
        r = yield Service(name="httpd", enabled=True)

        # Restart network service for interface eth0
        r = yield Service(name="network", state="restarted", arguments="eth0")

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - This implementation acts as a simplified proxy to underlying service managers.
        - Not all service managers support all options (e.g., systemd ignores arguments, pattern, runlevel).
        - For Windows targets, a different approach would be needed (not implemented here).
    """

    def __init__(self,
                 name: str,
                 state: str = None,
                 enabled: bool = None,
                 pattern: str = None,
                 arguments: str = "",
                 runlevel: str = "default",
                 sleep: int = None,
                 use: str = "auto",
                 guard: bool = True):

        self.name = name
        self.state = state
        self.enabled = enabled
        self.pattern = pattern
        self.arguments = arguments
        self.runlevel = runlevel
        self.sleep = sleep
        self.use = use
        self.guard = guard

        # Validate that at least one of state or enabled is provided
        if self.state is None and self.enabled is None:
            raise ValueError("At least one of 'state' and 'enabled' must be specified.")

        # Validate state parameter
        if self.state is not None and self.state not in ["started", "stopped", "restarted", "reloaded"]:
            raise ValueError("state must be one of 'started', 'stopped', 'restarted', 'reloaded'")

    def __repr__(self):
        return (f"Service(name={self.name!r}, "
                f"state={self.state!r}, "
                f"enabled={self.enabled!r}, "
                f"pattern={self.pattern!r}, "
                f"arguments={self.arguments!r}, "
                f"runlevel={self.runlevel!r}, "
                f"sleep={self.sleep!r}, "
                f"use={self.use!r}, "
                f"guard={self.guard!r})")

    def execute(self):
        # Build command based on parameters
        cmd_parts = []

        # Determine which service manager to use (simplified)
        if self.use == "auto":
            # In a real implementation, this would detect the service manager
            # For now, we'll default to systemctl for systemd-like systems
            service_cmd = "systemctl"
        else:
            service_cmd = self.use

        # Handle state operations
        if self.state is not None:
            if service_cmd == "systemctl":
                if self.state == "started":
                    cmd_parts.append(f"{service_cmd} start {self.name}")
                elif self.state == "stopped":
                    cmd_parts.append(f"{service_cmd} stop {self.name}")
                elif self.state == "restarted":
                    cmd_parts.append(f"{service_cmd} restart {self.name}")
                elif self.state == "reloaded":
                    cmd_parts.append(f"{service_cmd} reload {self.name}")
            else:
                # Fallback for other service managers
                cmd_parts.append(f"service {self.name} {self.state}")

                # Add arguments if provided (not for systemd)
                if self.arguments:
                    cmd_parts[-1] += f" {self.arguments}"

        # Handle enabled operations
        if self.enabled is not None:
            if service_cmd == "systemctl":
                enabled_state = "enable" if self.enabled else "disable"
                cmd_parts.append(f"{service_cmd} {enabled_state} {self.name}")
            else:
                # For other service managers, this might be different
                # This is a simplified approach
                enabled_state = "on" if self.enabled else "off"
                cmd_parts.append(f"chkconfig {self.name} {enabled_state}")

        # Join all commands with '&&' to execute them sequentially
        if cmd_parts:
            final_cmd = " && ".join(cmd_parts)
            r = yield Command(final_cmd, guard=self.guard)
            r.changed = True