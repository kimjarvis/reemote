
class Get_KernelVersion:
    """
    Returns the kernel name according to uname -r.

    **Examples:**

    .. code:: python

        class Get_kernelversion_example:
            def execute(self):
                from reemote.facts.server.get_ import Get_kernelversion
                r = yield Get_kernelversion()
                print(r.cp.stdout)

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r0 = yield Shell("uname -r")

