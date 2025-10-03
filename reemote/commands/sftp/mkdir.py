import asyncssh
from reemote.operation import Operation
from pathlib import PurePath
from typing import Union, Optional, Sequence, Tuple


class Mkdir:
    """
    A class to encapsulate the functionality of mkdir (make directory) in
    Unix-like operating systems with full SFTP attribute support.

    Attributes:
        path (str): The directory path to create.
        attrs (asyncssh.SFTPAttrs): SFTP attributes for the new directory.
        parents (bool): Whether to create parent directories if they don't exist (default: False).
        exist_ok (bool): Whether to not raise an error if the directory already exists (default: False).

    **Examples:**

    .. code:: python

        # Create a directory with specific permissions
        yield Mkdir(
            path='/home/user/newdir',
            attrs=asyncssh.SFTPAttrs(permissions=0o755)
        )

        # Create directory with owner and group
        yield Mkdir(
            path='/var/www/html',
            attrs=asyncssh.SFTPAttrs(
                permissions=0o755,
                uid=1000,
                gid=1000
            )
        )

        # Create directory with timestamps
        yield Mkdir(
            path='/tmp/timestamped',
            attrs=asyncssh.SFTPAttrs(
                permissions=0o755,
                atime=1672531200,  # Jan 1, 2023
                mtime=1672531200
            )
        )

        # Create directory recursively
        yield Mkdir(
            path='/very/deep/nested/directory',
            parents=True,
            exist_ok=True
        )

    Usage:
        This class is designed to be used in a generator-based workflow where
        commands are yielded for execution.
    """

    def __init__(self, path: str, attrs: Optional[asyncssh.SFTPAttrs] = None,
                 parents: bool = False, exist_ok: bool = False):
        self.path = path
        self.attrs = attrs or asyncssh.SFTPAttrs()
        self.parents = parents
        self.exist_ok = exist_ok

    def __repr__(self):
        attrs_repr = {}
        if self.attrs:
            # Only include non-None attributes in the representation
            for field in [
                'type', 'size', 'alloc_size', 'uid', 'gid', 'owner', 'group',
                'permissions', 'atime', 'atime_ns', 'crtime', 'crtime_ns',
                'mtime', 'mtime_ns', 'ctime', 'ctime_ns', 'acl', 'attrib_bits',
                'attrib_valid', 'text_hint', 'mime_type', 'nlink', 'untrans_name'
            ]:
                value = getattr(self.attrs, field, None)
                if value is not None:
                    if field == 'permissions':
                        attrs_repr[field] = oct(value)
                    else:
                        attrs_repr[field] = value

            if self.attrs.extended:
                attrs_repr['extended'] = self.attrs.extended

        return f"Mkdir(path={self.path!r}, attrs={attrs_repr!r}, parents={self.parents!r}, exist_ok={self.exist_ok!r})"

    @staticmethod
    async def _mkdir_callback(host_info, global_info, command, cp, caller):
        """Static callback method for directory creation with full attribute support"""

        # Validate host_info
        required_keys = ['host', 'username', 'password']
        for key in required_keys:
            if key not in host_info or host_info[key] is None:
                raise ValueError(f"Missing or invalid value for '{key}' in host_info.")

        # Validate caller attributes
        if caller.path is None:
            raise ValueError("The 'path' attribute of the caller cannot be None.")

        try:
            async with asyncssh.connect(**host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    # Check if directory already exists
                    try:
                        await sftp.stat(caller.path)
                        directory_exists = True
                    except (OSError, asyncssh.SFTPError):
                        directory_exists = False

                    # Handle existing directory
                    if directory_exists:
                        if caller.exist_ok:
                            return f"Directory {caller.path} already exists on {host_info['host']} (exist_ok=True)"
                        else:
                            raise FileExistsError(f"Directory {caller.path} already exists on {host_info['host']}")

                    # Create directory
                    if caller.parents:
                        # Create parent directories recursively
                        path_parts = PurePath(caller.path).parts
                        current_path = ""

                        for i, part in enumerate(path_parts):
                            current_path = str(PurePath(current_path, part)) if current_path else part

                            # Only apply full attributes to the final directory
                            if i == len(path_parts) - 1:
                                # Final directory - use provided attributes
                                attrs_to_use = caller.attrs
                            else:
                                # Intermediate directories - use default attributes
                                attrs_to_use = asyncssh.SFTPAttrs()
                                # But inherit permissions if specified, for consistency
                                if caller.attrs and caller.attrs.permissions is not None:
                                    attrs_to_use.permissions = caller.attrs.permissions

                            try:
                                await sftp.mkdir(current_path, attrs_to_use)
                            except (OSError, asyncssh.SFTPError) as e:
                                # If it's not the final directory and we get "file exists", that's fine
                                if current_path != caller.path and "file exists" in str(e).lower():
                                    continue
                                elif current_path == caller.path and "file exists" in str(
                                        e).lower() and caller.exist_ok:
                                    break
                                else:
                                    raise
                    else:
                        # Create single directory with specified attributes
                        await sftp.mkdir(caller.path, caller.attrs)

                    # Build result message
                    result_msg = f"Created directory {caller.path} on {host_info['host']}"

                    # Add attribute information to result message
                    attr_details = []
                    if caller.attrs:
                        if caller.attrs.permissions is not None:
                            attr_details.append(f"permissions={oct(caller.attrs.permissions)}")
                        if caller.attrs.uid is not None:
                            attr_details.append(f"uid={caller.attrs.uid}")
                        if caller.attrs.gid is not None:
                            attr_details.append(f"gid={caller.attrs.gid}")
                        if caller.attrs.owner is not None:
                            attr_details.append(f"owner={caller.attrs.owner}")
                        if caller.attrs.group is not None:
                            attr_details.append(f"group={caller.attrs.group}")
                        if caller.attrs.atime is not None:
                            attr_details.append(f"atime={caller.attrs.atime}")
                        if caller.attrs.mtime is not None:
                            attr_details.append(f"mtime={caller.attrs.mtime}")

                    if attr_details:
                        result_msg += f" with attributes: {', '.join(attr_details)}"

                    return result_msg

        except (OSError, asyncssh.Error) as exc:
            raise  # Re-raise the exception to handle it in the caller

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._mkdir_callback, caller=self)
        r.executed = True
        r.changed = True  # Set to True since mkdir changes the system state
        return r