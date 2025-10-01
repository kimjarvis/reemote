class Delete:
    """
    A class to manage lxc operations from the local host.

    Attributes:
        vm (str): Name of the virtual machine
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
        su (bool): If `True`, the commands will be executed with `su` privileges.

    **Examples:**

    .. code:: python

        yield Delete(vm='debian-vm2')

    Usage:
        Delete image.

    Notes:
        - Commands are constructed based on the `present`, `sudo`, and `su` flags.
        - The `changed` flag is set if the package state changes after execution.
    """

    def __init__(self,
                 vm: str,
                 sudo: bool = False,
                 su: bool = False):
        self.vm = vm
        self.sudo: bool = sudo
        self.su: bool = su

    def __repr__(self) -> str:
        return (f"Delete("
                f"vm={self.vm!r}, "
                f"sudo={self.sudo!r}, "
                f"su={self.su!r}"
                f")")

    def execute(self):
        from reemote.operations.server.shell import Shell
        yield Shell(f"lxc delete {self.vm}",sudo=self.sudo,su=self.su)
