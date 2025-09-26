import asyncssh
from reemote.operation import Operation


class Get_fstat:
    """
    A class to encapsulate the functionality of getting file attributes
    using SFTP fstat (stat on an open file handle) in Unix-like operating systems.

    Attributes:
        file_handle: The open file handle to get attributes for.
        flags (int): Flags indicating attributes of interest (SFTPv4 or later)

    **Examples:**

    .. code:: python

        yield Get_fstat(file_handle=file_handle)

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution. The file attributes for each
        host will be returned in the operation result.

    Notes:
        The operation will execute on all hosts in the current execution context.
        The file handle must be an open SFTP file handle obtained from a previous operation.
    """

    def __init__(self, file_handle=None, flags: int = None):
        self.file_handle = file_handle
        self.flags = flags

    def __repr__(self):
        return f"Get_fstat(file_handle={self.file_handle!r}, flags={self.flags!r})"

    @staticmethod
    async def _getfstat_callback(host_info, global_info, command, cp, caller):
        """Static callback method for getting file attributes from an open file handle"""
        async with asyncssh.connect(**host_info) as conn:
            async with conn.start_sftp_client() as sftp:
                # Get file attributes using fstat on the open file handle
                if caller.file_handle:
                    if caller.flags:
                        file_attrs = await caller.file_handle.fstat(caller.flags)
                    else:
                        file_attrs = await caller.file_handle.fstat()
                    return file_attrs
                else:
                    raise ValueError("File handle must be provided for fstat operation")

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._getfstat_callback, caller=self)
        r.executed = True
        r.changed = False
        return r