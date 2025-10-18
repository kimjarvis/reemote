# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class Debug:
    """
    A class to encapsulate the functionality of the Ansible debug module.
    This module prints statements during execution and can be useful for debugging variables
    or expressions without necessarily halting the execution flow.

    Attributes:
        msg (str or list): The customized message(s) that is printed. If omitted, prints a generic message.
        var (str): A variable name to debug. Mutually exclusive with the msg option.
        verbosity (int): A number that controls when the debug is run. Higher values require more verbose flags to display.

    **Examples:**

    .. code:: python

        # Print a custom message
        yield Debug(msg="System has gateway")

        # Debug a variable
        yield Debug(var="result")

        # Print with specific verbosity level
        yield Debug(var="hostvars[inventory_hostname]", verbosity=4)

        # Print multiple messages
        yield Debug(msg=[
            "Provisioning based on YOUR_KEY which is: {{ lookup('env', 'YOUR_KEY') }}",
            "These servers were built using the password of '{{ password_used }}'. Please retain this for later use."
        ])

    Usage:
        This class is designed to be used in a generator-based workflow where debug statements are yielded for execution.

    Notes:
        - The `var` parameter is evaluated in Jinja2 context and has implicit {{ }} wrapping.
        - Verbosity controls when the debug output is shown based on the verbosity level specified during execution.
    """

    def __init__(self,
                 msg: str or list = "Hello world!",
                 var: str = None,
                 verbosity: int = 0):
        if msg is not None and var is not None:
            raise ValueError("msg and var are mutually exclusive")

        self.msg = msg
        self.var = var
        self.verbosity = verbosity

    def __repr__(self):
        return (f"Debug(msg={self.msg!r}, "
                f"var={self.var!r}, verbosity={self.verbosity!r})")

    def execute(self):
        # In a real implementation, this would handle the debug output based on verbosity
        # For now, we'll just yield a command that represents the debug action
        debug_info = {
            'msg': self.msg,
            'var': self.var,
            'verbosity': self.verbosity
        }

        # This is a placeholder - in practice, this would interface with the logging/debug system
        r = yield Command(f"debug: {debug_info}", guard=True, sudo=False, su=False)
        r.changed = False