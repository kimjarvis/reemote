
class Get_Arch:
    """
    Returns the system architecture according to ``uname``.

    **Examples:**

    .. code:: python

        yield Get_Arch()

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r0 = yield Shell("uname -m")

