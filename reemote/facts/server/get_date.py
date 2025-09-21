
class Get_Date:
    """
    Returns the current datetime on the server.

    **Examples:**

    .. code:: python

        class Get_arch_example:
            def execute(self):
                from reemote.facts.server.get_date import Get_Date
                r = yield Get_Date()
                print(r.cp.stdout)

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        ISO_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"
        r0 = yield Shell(f"date +'{ISO_DATE_FORMAT}'")

