import asyncssh
from reemote.operation import Operation
from typing import Optional


class Write_file:
    """
    A class to encapsulate the functionality of writing files in Unix-like operating systems.
    It allows users to specify text to be written to a file with full SFTP attribute support.

    Attributes:
        path (str): The file path where content is to be written.
        text (str): The file content.
        attrs (asyncssh.SFTPAttrs): SFTP attributes for the new file.

    **Examples:**

    .. code:: python

        # Create a file from text with default permissions
        r = yield Write_file(path='example.txt', text='Hello World!')

        # Create a file with specific permissions
        r = yield Write_file(
            path='script.sh',
            text='#!/bin/bash\necho "Hello World"',
            attrs=asyncssh.SFTPAttrs(permissions=0o755)
        )

        # Create a file with owner and timestamps
        r = yield Write_file(
            path='config.json',
            text='{"key": "value"}',
            attrs=asyncssh.SFTPAttrs(
                permissions=0o644,
                uid=1000,
                gid=1000,
                mtime=1672531200
            )
        )

        # Verify the file content
        r = yield Shell("cat example.txt")
        print(r.cp.stdout)

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.
    """

    def __init__(self, path: str, text: str, attrs: Optional[asyncssh.SFTPAttrs] = None):
        self.path = path
        self.text = text
        self.attrs = attrs or asyncssh.SFTPAttrs()

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

        return f"Write_file(path={self.path!r}, text={self.text[:50] + '...' if len(self.text) > 50 else self.text!r}, attrs={attrs_repr!r})"

    @staticmethod
    async def _write_file_callback(host_info, global_info, command, cp, caller):
        """Static callback method for file writing with full attribute support"""

        # Validate host_info
        required_keys = ['host', 'username', 'password']
        for key in required_keys:
            if key not in host_info or host_info[key] is None:
                raise ValueError(f"Missing or invalid value for '{key}' in host_info.")

        # Validate caller attributes
        if caller.path is None:
            raise ValueError("The 'path' attribute of the caller cannot be None.")
        if caller.text is None:
            raise ValueError("The 'text' attribute of the caller cannot be None.")

        try:
            # Connect to the SSH server
            async with asyncssh.connect(**host_info) as conn:
                print("Connected successfully. Starting SFTP session...")
                # Start an SFTP session
                async with conn.start_sftp_client() as sftp:
                    print(f"Writing content to {caller.path}...")

                    # Open the remote file in write mode and write the content
                    async with sftp.open(caller.path, 'w') as remote_file:
                        r = await remote_file.write(caller.text)
                        print(f"Successfully wrote file {caller.path} on {host_info['host']} (bytes: {r})")

                    # Apply attributes after writing the file
                    if caller.attrs and any(
                            getattr(caller.attrs, field) is not None
                            for field in [
                                'type', 'size', 'alloc_size', 'uid', 'gid', 'owner', 'group',
                                'permissions', 'atime', 'atime_ns', 'crtime', 'crtime_ns',
                                'mtime', 'mtime_ns', 'ctime', 'ctime_ns', 'acl', 'attrib_bits',
                                'attrib_valid', 'text_hint', 'mime_type', 'nlink', 'untrans_name',
                                'extended'
                            ]
                    ):
                        print(f"Applying attributes to {caller.path}...")
                        await sftp.setstat(caller.path, caller.attrs)
                        print(f"Successfully applied attributes to {caller.path}")

        except (OSError, asyncssh.Error) as exc:
            print(f'SFTP operation failed on {host_info["host"]}: {str(exc)}')
            raise

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._write_file_callback, caller=self)
        r.executed = True
        r.changed = True
        return r