
from reemote.operation import Operation
from typing import Callable


class Callback:
    """
    A class to encapsulate the functionality of python callbacks.
    It allows users to specify an asynch callable function that is executed for all hosts.
    additional command-line options, and the ability to execute the command with elevated privileges (`sudo`).

    Attributes:

        guard (bool): If `False` the commands will not be executed.


    **Examples:**

    .. code:: python

        async def callable_function(host_info, global_info, command, cp, caller):
            if host_info["host"] == caller.host:
                print(f"callback called for host {caller.host}")

        class Demonstrate_callback:
            def execute(self):
                from reemote.operations.server.callback import Callback
                from reemote.operations.server.shell import Shell
                r = yield Shell("echo 'Hello World!'")
                print(r.cp.stdout)
                yield Callback(host="10.156.135.16", callback=callable_function)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.

    Notes:
        - Commands are constructed based on the `sudo`, and `su` flags.

    """
    def __init__(self, host: str=None,
                 callback: Callable = None,
                 guard: bool = True,):
        self.host = host
        self.callback = callback
        self.guard = guard
        self.callback_name = callback.__name__

    def __repr__(self):
        return (f"Callback(host={self.host!r}, "
                f"guard={self.guard!r}, "
                f"callback={self.callback_name!r})")

    def execute(self):
        from reemote.operation import Operation
        r = yield Operation(f"{self}", local=True, callback=self.callback, caller=self, guard=self.guard)
        r.executed=True
        r.changed=True
