
class Get_Date:
    """
    Returns the current datetime on the server.

    **Examples:**

    .. code:: python

        yield Get_Date()

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        ISO_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"
        r0 = yield Shell(f"date +'{ISO_DATE_FORMAT}'")

