
class Get_Home:
    """
    Returns the home directory of the current user.

    **Examples:**

    .. code:: python

        class Get_home_example:
            def execute(self):
                from reemote.facts.server.get_ import Get_home
                r = yield Get_home()
                print(r.cp.stdout)

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r0 = yield Shell("echo /home/kim")

