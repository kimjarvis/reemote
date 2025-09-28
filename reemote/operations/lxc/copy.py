from reemote.operation import Operation
class Copy:
    """
    A class to manage lxc operations from the local host.

    Attributes:
        vm (str): Name of the virtual machine
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
        su (bool): If `True`, the commands will be executed with `su` privileges.

    **Examples:**

    .. code:: python

        yield Copy(image='images:af547c023b88',alias='debian')

    Usage:
        Copy image to alias.

    Notes:
        - Commands are constructed based on the `present`, `sudo`, and `su` flags.
        - The `changed` flag is set if the package state changes after execution.
    """

    def __init__(self,
                 image: str,
                 alias: str = None,
                 sudo: bool = False,
                 su: bool = False):
        self.image = image
        self.alias = alias
        self.sudo: bool = sudo
        self.su: bool = su

    def __repr__(self) -> str:
        return (f"Copy("
                f"image={self.image!r}, "
                f"sudo={self.sudo!r}, "
                f"su={self.su!r}"
                f")")

    def execute(self):
        from reemote.operations.server.shell import Shell
        yield Shell(f"sudo lxc image {self.image} local: --alias {self.alias}")
