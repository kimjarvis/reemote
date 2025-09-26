import asyncssh
from reemote.operation import Operation
from typing import Optional, Dict, Any


class Setstat:
    """
    A class to encapsulate the functionality of setstat for setting file attributes
    using SFTP setstat in Unix-like operating systems.

    Attributes:
        path (str): The path of the file to set attributes for.
        attrs (dict): Dictionary of attributes to set on the file.

    **Examples:**

    .. code:: python

        yield Setstat(
            path="/path/to/file.txt",
            attrs={
                "permissions": 0o644,
                "uid": 1000,
                "gid": 1000,
                "mtime": 1672531200
            }
        )

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution. The operation result will indicate
        whether the attribute setting was successful for each host.

    Notes:
        Common attributes include: permissions, uid, gid, mtime, atime, size.
    """

    def __init__(self, path: str, attrs: Dict[str, Any]):
        self.path = path
        self.attrs = attrs

    def __repr__(self):
        return f"Setstat(path={self.path!r}, attrs={self.attrs!r})"

    @staticmethod
    def _dict_to_sftp_attrs(attrs_dict: Dict[str, Any]) -> asyncssh.SFTPAttrs:
        """Convert dictionary to SFTPAttrs object"""
        attrs = asyncssh.SFTPAttrs()

        # Set available attributes from dictionary
        if 'permissions' in attrs_dict and attrs_dict['permissions'] is not None:
            # Handle both octal string and integer permissions
            perms = attrs_dict['permissions']
            if isinstance(perms, str) and perms.startswith('0o'):
                attrs.permissions = int(perms, 8)
            else:
                attrs.permissions = int(perms)

        if 'uid' in attrs_dict and attrs_dict['uid'] is not None:
            attrs.uid = attrs_dict['uid']
        if 'gid' in attrs_dict and attrs_dict['gid'] is not None:
            attrs.gid = attrs_dict['gid']
        if 'atime' in attrs_dict and attrs_dict['atime'] is not None:
            attrs.atime = attrs_dict['atime']
        if 'mtime' in attrs_dict and attrs_dict['mtime'] is not None:
            attrs.mtime = attrs_dict['mtime']
        if 'size' in attrs_dict and attrs_dict['size'] is not None:
            attrs.size = attrs_dict['size']
        if 'nlink' in attrs_dict and attrs_dict['nlink'] is not None:
            attrs.nlink = attrs_dict['nlink']

        return attrs

    @staticmethod
    async def _setstat_callback(host_info, global_info, command, cp, caller):
        """Static callback method for setting file attributes"""

        # Validate host_info (matching Rmdir error handling)
        required_keys = ['host', 'username', 'password']
        for key in required_keys:
            if key not in host_info or host_info[key] is None:
                raise ValueError(f"Missing or invalid value for '{key}' in host_info.")

        # Validate caller attributes (matching Rmdir error handling)
        if caller.path is None:
            raise ValueError("The 'path' attribute of the caller cannot be None.")

        if caller.attrs is None or not isinstance(caller.attrs, dict):
            raise ValueError("The 'attrs' attribute must be a non-empty dictionary.")

        try:
            # Connect to the SSH server and set file attributes
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    # Convert dictionary to SFTPAttrs
                    sftp_attrs = caller._dict_to_sftp_attrs(caller.attrs)

                    # Set attributes using setstat
                    await sftp.setstat(caller.path, sftp_attrs)
        except (OSError, asyncssh.Error) as exc:
            raise  # Re-raise the exception to handle it in the caller

    def execute(self):
        r = yield Operation(
            f"{self}",
            local=True,
            callback=self._setstat_callback,
            caller=self
        )
        r.executed = True
        r.changed = True  # This operation changes the system state
        return r