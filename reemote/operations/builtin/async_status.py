# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class AsyncStatus:
    """
    A class to encapsulate the functionality of the ansible.builtin.async_status module.
    This module gets the status of an asynchronous task and can also clean up the async job cache.

    Attributes:
        jid (str): Job or task identifier (required).
        mode (str): If "status", obtain the status. If "cleanup", clean up the async job cache.
                   Defaults to "status".

    **Examples:**

    .. code:: python

        # Wait for asynchronous job to end
        r = yield AsyncStatus(jid=dnf_sleeper.ansible_job_id)
        # Check if job is finished
        if r.cp.finished:
            print("Job completed")

        # Clean up async file
        r = yield AsyncStatus(jid=dnf_sleeper.ansible_job_id, mode="cleanup")

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - The started and finished return values return True or False instead of 1 or 0.
        - This module is supported for both POSIX and Windows targets.
        - The module has a corresponding action plugin.
    """

    def __init__(self,
                 jid: str,
                 mode: str = "status"):
        """
        Initialize the AsyncStatus object.

        Args:
            jid (str): Job or task identifier (required).
            mode (str): Operation mode - either "status" or "cleanup". Defaults to "status".
        """
        self.jid = jid
        self.mode = mode

        # Validate mode parameter
        if self.mode not in ["status", "cleanup"]:
            raise ValueError("mode must be either 'status' or 'cleanup'")

    def __repr__(self):
        return f"AsyncStatus(jid={self.jid!r}, mode={self.mode!r})"

    def execute(self):
        """
        Execute the async_status command.

        Returns:
            Generator yielding Command object for execution.
        """
        # Construct the ansible command
        if self.mode == "cleanup":
            cmd = f"ansible.builtin.async_status jid={self.jid} mode=cleanup"
        else:
            cmd = f"ansible.builtin.async_status jid={self.jid}"

        r = yield Command(cmd)
        r.changed = True