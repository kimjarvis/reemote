
class Get_KernelVersion:
    """
    Returns the kernel name according to uname -r.

    **Examples:**

    .. code:: python

        yield Get_kernelversion()

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r0 = yield Shell("uname -r")

