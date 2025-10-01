class Get_status:
    """
    Returns the IP address of a lxc container from the local host.

    Attributes:
        vm (str): Name of the virtual machine

    **Examples:**

    .. code:: python

        yield Get_ip(vm='debian-vm2)

    """
    def __init__(self,
                 vm: str,
                 sudo: bool = False,
                 su: bool = False):
        self.vm = vm
        self.sudo: bool = sudo
        self.su: bool = su

    def __repr__(self) -> str:
        return (f"Stop("
                f"vm={self.vm!r}, "
                f"sudo={self.sudo!r}, "
                f"su={self.su!r}"
                f")")


    def execute(self):
        from reemote.operations.server.shell import Shell
        r = yield Shell(f"lxc info {self.vm} | grep -oP 'Status: \K\S+'")
        r.cp.stdout = r.cp.stdout.rstrip('\n')
