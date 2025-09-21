
class Get_Kernel:
    """
    Returns the kernel name according to uname -s.

    **Examples:**

    .. code:: python

        class Get_kernel_example:
            def execute(self):
                from reemote.facts.server.get_ import Get_kernel
                r = yield Get_kernel()
                print(r.cp.stdout)

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r0 = yield Shell("uname -s")

