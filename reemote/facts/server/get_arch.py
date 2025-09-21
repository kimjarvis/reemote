
class Get_Arch:
    """
    Returns the system architecture according to ``uname``.

    **Examples:**

    .. code:: python

        class Get_arch_example:
            def execute(self):
                from reemote.facts.server.get_arch import Get_Arch
                r = yield Get_Arch()
                print(r.cp.stdout)

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r0 = yield Shell("uname -m")

