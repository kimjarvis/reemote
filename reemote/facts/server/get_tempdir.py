
class Get_TmpDir:
    """
    Returns the temporary directory of the current server, if configured.

    **Examples:**

    .. code:: python

        class Get_tmpdir_example:
            def execute(self):
                from reemote.facts.server.get_ import Get_tmpdir
                r = yield Get_tmpdir()
                print(r.cp.stdout)

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r0 = yield Shell("echo ")

