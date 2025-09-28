class Get_list:
    """
    Returns a list of lxc containers from the local host.

    Attributes:

    **Examples:**

    .. code:: python

        yield Get_list()

    """
    def __init__(self,
                 sudo: bool = False,
                 su: bool = False):
        self.sudo: bool = sudo
        self.su: bool = su

    def __repr__(self) -> str:
        return (f"Stop("
                f"sudo={self.sudo!r}, "
                f"su={self.su!r}"
                f")")


    def execute(self):
        from reemote.operations.server.shell import Shell
        yield Shell("lxc list --format csv | cut -d, -f1",sudo=self.sudo,su=self.su)
