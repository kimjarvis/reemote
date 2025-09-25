import asyncssh
from reemote.operation import Operation
from typing import Optional, Dict, Any


class Setstat:
    """
    A class to encapsulate the functionality of setstat for setting file attributes
    using SFTP setstat in Unix-like operating systems.

    Attributes:
        hosts (list): The list of hosts on which to set file attributes.
        file_path (str): The path of the file to set attributes for.
        attrs (dict): Dictionary of attributes to set on the file.

    **Examples:**

    .. code:: python

        yield Setstat(
            hosts=["10.156.135.16", "10.156.135.17"],
            file_path="/path/to/file.txt",
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
        If hosts is None or empty, the operation will execute on the current host.
        Common attributes include: permissions, uid, gid, mtime, atime, size.
    """

    def __init__(self, path: str, attrs: Dict[str, Any], hosts: list = None):
        self.path = path
        self.attrs = attrs
        self.hosts = hosts

    def __repr__(self):
        return f"Setstat(file_path={self.path!r}, attrs={self.attrs!r}, hosts={self.hosts!r})"

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
    async def _setstat_callback(host_info, sudo_global, command, cp, caller):
        print(f"{caller}")
        """Static callback method for setting file attributes"""
        if (caller.hosts is None or
                not caller.hosts or
                host_info["host"] in caller.hosts):

            print(f"Setting file attributes on host {host_info['host']} for path {caller.path}")

            async def run_client() -> None:
                try:
                    async with asyncssh.connect(**host_info) as conn:
                        async with conn.start_sftp_client() as sftp:
                            # Convert dictionary to SFTPAttrs
                            sftp_attrs = caller._dict_to_sftp_attrs(caller.attrs)

                            # Set attributes using setstat
                            await sftp.setstat(caller.path, sftp_attrs)
                            return {"success": True, "message": f"Attributes set successfully for {caller.path}"}
                except (OSError, asyncssh.Error) as exc:
                    error_msg = f'SFTP setstat operation failed on {host_info["host"]}: {str(exc)}'
                    print(error_msg)
                    return {"success": False, "error": error_msg}

            try:
                result = await run_client()
                return result
            except KeyboardInterrupt:
                print('Operation interrupted by user.')
                raise
            except Exception as e:
                error_msg = f"An error occurred on {host_info['host']}: {e}"
                print(error_msg)
                return {"success": False, "error": error_msg}

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._setstat_callback, caller=self)
        r.executed = True
        r.changed = True  # This operation changes the system state
        return r