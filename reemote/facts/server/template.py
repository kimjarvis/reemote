
class Get_{{ fact1 }}:
    """
    {{ explain }}

    **Examples:**

    .. code:: python

        class Get_{{ fact }}_example:
            def execute(self):
                from reemote.facts.server.get_{{ factl }} import Get_{{ fact }}
                r = yield Get_{{ fact}}()
                print(r.cp.stdout)

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r0 = yield Shell("{{ fun }}")

