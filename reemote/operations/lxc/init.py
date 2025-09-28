from reemote.operation import Operation
class Init:
    """
    A class to manage lxc operations from the local host.

    Attributes:
        vm (str): Name of the virtual machine
        alias (str): Alias of the virtual machine type
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
        su (bool): If `True`, the commands will be executed with `su` privileges.

    **Examples:**

    .. code:: python

        yield Init(alias="debian",vm="debian-vm10")

    Usage:
        Initialise container

    Notes:
        - Commands are constructed based on the `present`, `sudo`, and `su` flags.
        - The `changed` flag is set if the package state changes after execution.
    """

    def __init__(self,
                 vm: str,
                 alias: str,
                 sudo: bool = False,
                 su: bool = False):
        self.vm = vm
        self.alias = alias
        self.sudo: bool = sudo
        self.su: bool = su

    def __repr__(self) -> str:
        return (f"Stop("
                f"vm={self.vm!r}, "
                f"alias={self.alias!r}, "
                f"sudo={self.sudo!r}, "
                f"su={self.su!r}"
                f")")

    def execute(self):
        from reemote.operations.server.shell import Shell
        yield Shell(f"lxc init {self.alias} {self.vm}",sudo=self.sudo,su=self.su)
