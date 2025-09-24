
class Get_User:
    """
    Returns the name of the current user.

    **Examples:**

    .. code:: python

        yield Get_user()

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r0 = yield Shell("whoami")

