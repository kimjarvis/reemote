class Get_OS:
    """
    A class to obtain information about the OS.

    Attributes:
        field (str): The name of the field to operate on.
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
        su (bool): If `True`, the commands will be executed with `su` privileges.

    **Examples:**

    .. code:: python

        yield Get_OS("NAME")

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.
        It supports adding or removing packages based on the `present` flag and allows privilege escalation via `sudo` or `su`.

    Notes:
        - Commands are constructed based on the `present`, `sudo`, and `su` flags.
        - The `changed` flag is set if the package state changes after execution.
    """

    def __init__(self,
                 field: str = "PRETTY_NAME",
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):
        self.field = field
        self.guard: bool = guard
        self.sudo: bool = sudo
        self.su: bool = su

    def execute(self):
        from reemote.operations.server.shell import Shell
        import re

        # Execute the shell command to read /etc/os-release
        r0 = yield Shell("cat /etc/os-release")

        # Define a regex pattern for extracting the specified field
        field_pattern = rf'{self.field}="([^"]+)"'

        # Search for the specified field in the output
        match = re.search(field_pattern, r0.cp.stdout)

        if match:
            # Extract and return the value of the specified field
            result = match.group(1)

            # Handle special cases for rolling releases (if applicable)
            if self.field == "VERSION_ID" and result.lower() == "rolling":
                result = "Rolling Release"

            r0.cp.stdout = result
        else:
            # Handle failure to extract the specified field
            r0.cp.stdout = f"Failed to extract field '{self.field}' from OS details."

        return r0.cp.stdout

    def __repr__(self):
        return (f"Get_OS(field={self.filed!r}, "
                f"guard={self.guard!r}, "                                
                f"sudo={self.sudo!r}, su={self.su!r})")
