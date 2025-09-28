class Get_list:
    """
    Returns a list of lxc containers from the local host.

    Attributes:

    **Examples:**

    .. code:: python

        yield Get_list()

    """
    def __init__(self,
                 ):
        self.vm = vm

    def __repr__(self):
        return (f"Get_list("
                ")")

    def execute(self):
        from reemote.operations.server.shell import Shell
        r = yield Shell("lxc list --format csv | cut -d, -f1")
        # ip_address = r.cp.stdout.rstrip('\n')
        # print("IP address: ", ip_address)
