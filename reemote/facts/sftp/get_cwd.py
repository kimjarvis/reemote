import asyncssh
from reemote.operation import Operation


class Get_cwd:
    """
    A class to encapsulate the functionality of cwd (get current working directory)
    in Unix-like operating systems.

    **Examples:**

    .. code:: python

        yield Getcwd()

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution. The current working directory for each
        host will be returned in the operation result.

    Notes:
        The operation will execute on all hosts in the current execution context.
    """

    def __init__(self):
        pass

    def __repr__(self):
        return f"Getcwd()"

    @staticmethod
    async def _getcwd_callback(host_info, global_info, command, cp, caller):
        """Static callback method for getting current working directory"""
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                # Get the current remote working directory
                cwd = await sftp.getcwd()
                return cwd

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._getcwd_callback, caller=self)
        r.executed = True
        r.changed = False
        return r