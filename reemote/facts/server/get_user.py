
class Get_User:
    """
    Returns the name of the current user.

    **Examples:**

    .. code:: python

        class Get_user_example:
            def execute(self):
                from reemote.facts.server.get_ import Get_user
                r = yield Get_user()
                print(r.cp.stdout)

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r0 = yield Shell("whoami")

