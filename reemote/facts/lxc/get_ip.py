class Get_ip:
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
                 ):
        self.vm = vm

    def __repr__(self):
        return (f"Get_ip("
                f"vm={self.vm!r}, "
                ")")

    def execute(self):
        from reemote.operations.server.shell import Shell
        r = yield Shell("lxc info centos-vm3 | grep -oE 'inet: *([0-9]{1,3}\.){3}[0-9]{1,3}' | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | head -1")
        # ip_address = r.cp.stdout.rstrip('\n')
        # print("IP address: ", ip_address)
