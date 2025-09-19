
class Get_Path:
    """
    Returns the path environment variable of the current user.

    **Examples:**

    .. code:: python

        class Get_path_example:
            def execute(self):
                from reemote.facts.server.get_ import Get_path
                r = yield Get_path()
                print(r.cp.stdout)

    """
    def execute(self):
        from reemote.operations.server.shell import Shell
        r0 = yield Shell("echo /home/kim/miniconda3/envs/reemote/bin:/home/kim/miniconda3/condabin:/home/kim/miniconda3/bin:/usr/local/bin:/usr/bin:/bin:/usr/games")

