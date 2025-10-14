# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command
import time


class Reboot:
    """
    A class to encapsulate the functionality of rebooting machines in Unix-like operating systems.
    It allows users to specify various reboot parameters such as delays, messages, custom commands,
    and timeout settings. The class waits for the machine to go down, come back up, and respond to commands.

    Attributes:
        boot_time_command (str): Command to run that returns a unique string indicating the last time the system was booted.
        connect_timeout (int): Maximum seconds to wait for a successful connection to the managed hosts before trying again.
        msg (str): Message to display to users before reboot.
        post_reboot_delay (int): Seconds to wait after the reboot command was successful before attempting to validate the system rebooted successfully.
        pre_reboot_delay (int): Seconds to wait before reboot. Passed as a parameter to the reboot command.
        reboot_command (str): Command to run that reboots the system, including any parameters passed to the command.
        reboot_timeout (int): Maximum seconds to wait for machine to reboot and respond to a test command.
        search_paths (list): Paths to search on the remote machine for the shutdown command.
        test_command (str): Command to run on the rebooted host and expect success from to determine the machine is ready for further tasks.

    **Examples:**

    .. code:: python

        # Unconditionally reboot the machine with all defaults
        r = yield Reboot()

        # Reboot a slow machine that might have lots of updates to apply
        r = yield Reboot(reboot_timeout=3600)

        # Reboot a machine with shutdown command in unusual place
        r = yield Reboot(search_paths=['/lib/molly-guard'])

        # Reboot machine using a custom reboot command
        r = yield Reboot(
            reboot_command="launchctl reboot userspace",
            boot_time_command="uptime | cut -d ' ' -f 5"
        )

        # Reboot machine and send a message
        r = yield Reboot(msg="Rebooting machine in 5 seconds")

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - PATH is ignored on the remote node when searching for the shutdown command. Use search_paths to specify locations to search if the default paths do not work.
        - For Windows targets, use the ansible.windows.win_reboot module instead.
    """

    def __init__(self,
                 boot_time_command: str = "cat /proc/sys/kernel/random/boot_id",
                 connect_timeout: int = None,
                 msg: str = "Reboot initiated by Ansible",
                 post_reboot_delay: int = 0,
                 pre_reboot_delay: int = 0,
                 reboot_command: str = None,
                 reboot_timeout: int = 600,
                 search_paths: list = ["/sbin", "/bin", "/usr/sbin", "/usr/bin", "/usr/local/sbin"],
                 test_command: str = "whoami"):

        self.boot_time_command = boot_time_command
        self.connect_timeout = connect_timeout
        self.msg = msg
        self.post_reboot_delay = post_reboot_delay
        self.pre_reboot_delay = pre_reboot_delay
        self.reboot_command = reboot_command
        self.reboot_timeout = reboot_timeout
        self.search_paths = search_paths
        self.test_command = test_command

    def __repr__(self):
        return (f"Reboot(boot_time_command={self.boot_time_command!r}, "
                f"connect_timeout={self.connect_timeout!r}, "
                f"msg={self.msg!r}, "
                f"post_reboot_delay={self.post_reboot_delay!r}, "
                f"pre_reboot_delay={self.pre_reboot_delay!r}, "
                f"reboot_command={self.reboot_command!r}, "
                f"reboot_timeout={self.reboot_timeout!r}, "
                f"search_paths={self.search_paths!r}, "
                f"test_command={self.test_command!r})")

    def execute(self):
        start_time = time.time()

        # Get initial boot time to compare later
        boot_time_result = yield Command(self.boot_time_command)
        initial_boot_time = boot_time_result.cp.stdout.strip() if boot_time_result.cp else ""

        # Construct reboot command
        if self.reboot_command:
            reboot_cmd = self.reboot_command
        else:
            # Default reboot command with delay and message
            if self.pre_reboot_delay > 0:
                reboot_cmd = f"shutdown -r +{max(0, self.pre_reboot_delay // 60)} '{self.msg}'"
            else:
                reboot_cmd = f"shutdown -r now '{self.msg}'"

        # Execute reboot command
        yield Command(reboot_cmd, sudo=True)

        # Wait for post_reboot_delay
        if self.post_reboot_delay > 0:
            time.sleep(self.post_reboot_delay)

        # Wait for system to go down and come back up
        elapsed = 0
        rebooted = False

        while elapsed < self.reboot_timeout:
            try:
                # Try to connect and run test command
                test_result = yield Command(self.test_command, guard=False)

                if test_result.cp and test_result.cp.returncode == 0:
                    # System is back up, verify it's actually rebooted
                    boot_time_result = yield Command(self.boot_time_command, guard=False)
                    current_boot_time = boot_time_result.cp.stdout.strip() if boot_time_result.cp else ""

                    if current_boot_time != initial_boot_time:
                        rebooted = True
                        break

            except Exception:
                # Connection failed, system is probably down
                pass

            # Wait before retrying
            time.sleep(5)
            elapsed = time.time() - start_time

        # Set return values
        r = type('Result', (), {})()
        r.elapsed = int(time.time() - start_time)
        r.rebooted = rebooted
        r.changed = True