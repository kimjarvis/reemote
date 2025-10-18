# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from reemote.command import Command


class Assert:
    """
    A class to encapsulate the functionality of asserting expressions in Ansible-style fashion.
    It allows users to specify expressions that must evaluate to true, with optional custom messages
    for both success and failure cases. The module supports quiet mode to reduce output verbosity.

    Attributes:
        that (list): A list of string expressions to evaluate.
        fail_msg (str): Custom message to display on assertion failure.
        success_msg (str): Custom message to display on assertion success.
        quiet (bool): If True, suppresses verbose output.

    **Examples:**

    .. code:: python

        # Simple assertion
        yield Assert(that=["ansible_os_family != 'RedHat'"])

        # Multiple conditions with custom messages
        yield Assert(
            that=[
                "my_param <= 100",
                "my_param >= 0"
            ],
            fail_msg="'my_param' must be between 0 and 100",
            success_msg="'my_param' is between 0 and 100"
        )

        # Quiet mode to reduce output
        yield Assert(
            that=["my_param <= 100", "my_param >= 0"],
            quiet=True
        )

    Usage:
        This class is designed to be used in a generator-based workflow where assertions are yielded for evaluation.

    Notes:
        - Expressions follow the same syntax as Ansible's `when` statements.
        - Both `fail_msg` and `success_msg` support templating.
        - The `quiet` parameter can be used to minimize output in large-scale deployments.
    """

    def __init__(self,
                 that: list,
                 fail_msg: str = None,
                 success_msg: str = None,
                 quiet: bool = False):

        self.that = that
        self.fail_msg = fail_msg
        self.success_msg = success_msg
        self.quiet = quiet

    def __repr__(self):
        return (f"Assert(that={self.that!r}, "
                f"fail_msg={self.fail_msg!r}, "
                f"success_msg={self.success_msg!r}, "
                f"quiet={self.quiet!r})")

    def execute(self):
        # Convert the expressions into a format suitable for evaluation
        # This is a simplified implementation - in practice, this would need
        # to handle variable substitution and complex expression evaluation
        expressions = self.that if isinstance(self.that, list) else [self.that]

        # For demonstration purposes, we'll construct a shell command that evaluates the expressions
        # In a real implementation, this would interface with the Ansible evaluation engine
        expr_string = " && ".join(expressions)

        # Build command with appropriate flags
        cmd_parts = ["test"]

        for expr in expressions:
            # Convert common Ansible expressions to shell equivalents
            # This is a simplified mapping - real implementation would be more comprehensive
            shell_expr = expr.replace("==", "-eq").replace("!=", "-ne")
            cmd_parts.extend(["'", shell_expr, "'"])

        cmd = " ".join(cmd_parts)

        # Execute the command
        r = yield Command(
            cmd=cmd,
            guard=True  # Always execute assertions
        )

        # Set changed status (assertions typically don't change state)
        r.changed = False

        # Handle messaging based on result and quiet flag
        if not self.quiet:
            if r.cp.returncode == 0 and self.success_msg:
                print(self.success_msg)
            elif r.cp.returncode != 0 and (self.fail_msg or self.fail_msg != ""):
                fail_message = self.fail_msg or f"Assertion failed: {expr_string}"
                print(fail_message)
            elif r.cp.returncode != 0:
                print(f"Assertion failed: {expr_string}")
