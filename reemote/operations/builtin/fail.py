# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class Fail:
    """
    A class to encapsulate the functionality of the Ansible fail module.
    This module fails the progress with a custom message.
    It can be useful for bailing out when a certain condition is met using when.

    Attributes:
        msg (str): The customized message used for failing execution.
        guard (bool): If `False` the commands will not be executed.

    **Examples:**

    .. code:: python

        # Fail with a custom message
        yield Fail("The system may not be provisioned according to the CMDB status.")

        # Fail with default message
        yield Fail()

    Usage:
        This class is designed to be used in a generator-based workflow where failure conditions are yielded for execution.

    Notes:
        - This module is also supported for Windows targets.
        - This module has a corresponding action plugin.
    """

    def __init__(self,
                 msg: str = "Failed as requested from task",
                 guard: bool = True):
        self.msg = msg
        self.guard = guard

    def __repr__(self):
        return (f"Fail(msg={self.msg!r}, "
                f"guard={self.guard!r})")

    def execute(self):
        # Construct the command to fail with custom message
        cmd = f"echo '{self.msg}' >&2 && exit 1"
        r = yield Command(cmd, guard=self.guard)
        r.changed = False
        # Mark the result as failed
        r.failed = True
        r.msg = self.msg