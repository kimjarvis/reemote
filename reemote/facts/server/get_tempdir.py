
class Get_TmpDir:
    """
    Returns the temporary directory of the current server, if configured.

    **Examples:**

    .. code:: python

        yield Get_tmpdir()

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r0 = yield Shell("echo ")

