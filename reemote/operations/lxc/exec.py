class Exec:
    """
    A class to manage lxc operations from the local host.

    Attributes:
        vm (str): Name of the virtual machine
        cmd (str): Command to execute
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
        su (bool): If `True`, the commands will be executed with `su` privileges.

    **Examples:**

    .. code:: python

        yield Exec(vm='debian-vm2',cmd='apt-get update')

    Usage:
        Execute command in container.

    Notes:
        - Commands are constructed based on the `present`, `sudo`, and `su` flags.
        - The `changed` flag is set if the package state changes after execution.
    """

    def __init__(self,
                 vm: str,
                 cmd: str,
                 sudo: bool = False,
                 su: bool = False):
        self.cmd = cmd
        self.vm = vm
        self.sudo: bool = sudo
        self.su: bool = su

    def __repr__(self) -> str:
        return (f"Exec("
                f"vm={self.vm!r}, "
                f"cmd={self.cmd!r}, "
                f"sudo={self.sudo!r}, "
                f"su={self.su!r}"
                f")")

    def execute(self):
        from reemote.operations.server.shell import Shell
        yield Shell(f"lxc exec {self.vm} -- {self.cmd}",sudo=self.sudo,su=self.su)
