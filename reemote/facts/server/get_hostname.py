
class Get_Hostname:
    """
    Returns the current hostname of the server.

    **Examples:**

    .. code:: python

        yield Get_hostname()

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r0 = yield Shell("uname -n")

