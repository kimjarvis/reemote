
class Get_Hostname:
    """
    Returns the current hostname of the server.

    **Examples:**

    .. code:: python

        class Get_hostname_example:
            def execute(self):
                from reemote.facts.server.get_ import Get_hostname
                r = yield Get_hostname()
                print(r.cp.stdout)

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r0 = yield Shell("uname -n")

