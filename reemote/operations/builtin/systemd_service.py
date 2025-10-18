# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class SystemdService:
    """
    A class to manage systemd units (services, timers, etc.) on remote hosts.
    This class provides functionality similar to Ansible's systemd_service module,
    allowing control of systemd units through various operations like start, stop,
    restart, reload, enable, disable, mask, unmask, and daemon operations.

    Attributes:
        name (str): Name of the unit. When no extension is given, it is implied to be .service.
        state (str): Desired state of the unit (started, stopped, restarted, reloaded).
        enabled (bool): Whether the unit should start on boot.
        masked (bool): Whether the unit should be masked (impossible to start).
        daemon_reload (bool): Run daemon-reload before operations.
        daemon_reexec (bool): Run daemon_reexec before operations.
        force (bool): Override existing symlinks.
        no_block (bool): Don't wait for operation to finish.
        scope (str): Service manager scope (system, user, global).
        guard (bool): If False, commands will not be executed.

    **Examples:**

    .. code:: python

        # Start a service
        r = yield SystemdService(name="httpd", state="started")
        print(r.cp.stdout)

        # Stop a service
        r = yield SystemdService(name="cron", state="stopped")

        # Restart service with daemon reload
        r = yield SystemdService(name="crond", state="restarted", daemon_reload=True)

        # Enable a service
        r = yield SystemdService(name="httpd", enabled=True)

        # Mask a service
        r = yield SystemdService(name="unwanted-service", masked=True)

        # Force daemon reload
        r = yield SystemdService(daemon_reload=True)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - At least one of state, enabled, or masked is required when specifying a name.
        - Operations are executed in order: enable/disable -> mask/unmask -> state management.
        - Commands are constructed based on the provided parameters and scope.
    """

    def __init__(self,
                 name: str = None,
                 state: str = None,
                 enabled: bool = None,
                 masked: bool = None,
                 daemon_reload: bool = False,
                 daemon_reexec: bool = False,
                 force: bool = False,
                 no_block: bool = False,
                 scope: str = "system",
                 guard: bool = True):

        self.name = name
        self.state = state
        self.enabled = enabled
        self.masked = masked
        self.daemon_reload = daemon_reload
        self.daemon_reexec = daemon_reexec
        self.force = force
        self.no_block = no_block
        self.scope = scope
        self.guard = guard

        # Validate parameter combinations
        if name is None:
            if state is not None or enabled is not None or masked is not None:
                raise ValueError("name is required when state, enabled, or masked is specified")
        else:
            if state is None and enabled is None and masked is None:
                raise ValueError("At least one of state, enabled, or masked is required when name is specified")

    def __repr__(self):
        return (f"SystemdService(name={self.name!r}, "
                f"state={self.state!r}, "
                f"enabled={self.enabled!r}, "
                f"masked={self.masked!r}, "
                f"daemon_reload={self.daemon_reload!r}, "
                f"daemon_reexec={self.daemon_reexec!r}, "
                f"force={self.force!r}, "
                f"no_block={self.no_block!r}, "
                f"scope={self.scope!r}, "
                f"guard={self.guard!r})")

    def _build_command(self) -> str:
        """Build the systemctl command based on the provided parameters."""
        cmd_parts = ["systemctl"]

        # Add scope if not default
        if self.scope != "system":
            cmd_parts.append(f"--{self.scope}")

        # Add no-block flag
        if self.no_block:
            cmd_parts.append("--no-block")

        # Add force flag
        if self.force:
            cmd_parts.append("--force")

        # Handle daemon operations first
        if self.daemon_reexec:
            return " ".join(cmd_parts + ["daemon-reexec"])

        if self.daemon_reload:
            return " ".join(cmd_parts + ["daemon-reload"])

        # Handle unit operations
        if self.name:
            unit_name = self.name
            # Add .service extension if no extension is provided
            if "." not in unit_name:
                unit_name += ".service"

            # Handle enable/disable
            if self.enabled is not None:
                action = "enable" if self.enabled else "disable"
                enable_cmd = " ".join(cmd_parts + [action, unit_name])

            # Handle mask/unmask
            if self.masked is not None:
                action = "mask" if self.masked else "unmask"
                mask_cmd = " ".join(cmd_parts + [action, unit_name])

            # Handle state changes
            if self.state is not None:
                state_cmd = " ".join(cmd_parts + [self.state, unit_name])

            # Build command sequence based on operations needed
            commands = []
            if 'enable_cmd' in locals():
                commands.append(enable_cmd)
            if 'mask_cmd' in locals():
                commands.append(mask_cmd)
            if 'state_cmd' in locals():
                commands.append(state_cmd)

            return " && ".join(commands)

        return " ".join(cmd_parts)

    def execute(self):
        """Execute the systemd service command."""
        cmd = self._build_command()
        r = yield Command(cmd, guard=self.guard)
        r.changed = True