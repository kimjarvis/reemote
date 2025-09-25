
class Get_Kernel:
    """
    Returns the kernel name according to uname -s.

    **Examples:**

    .. code:: python

        yield Get_kernel()

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r0 = yield Shell("uname -s")

