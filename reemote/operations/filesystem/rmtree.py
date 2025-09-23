import asyncssh
from reemote.operation import Operation


class RmTree:
    """
    A class to encapsulate the functionality of rmtree in
    Unix-like operating systems.

    Attributes:
        path (str): The directory path to change to.
        hosts (list): The list of hosts on which the directory change is to be performed.

    **Examples:**

    .. code:: python

            class Rmtree::
                def execute(self):
                    yield Rmtree(
                        path='/home/user/hfs',
                        hosts=["10.156.135.16"]
                    )

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution.

    Notes:
        If hosts is None or empty, the operation will execute on the current host.
    """
