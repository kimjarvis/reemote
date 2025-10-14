# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import asyncssh
from asyncssh.sftp import SFTPAttrs
class Setstat:
    def __init__(self, path: str, attrs, follow_symlinks: bool = True):
        self.path = path
        self.attrs = attrs
        self.follow_symlinks = follow_symlinks

        # Convert attrs to a dictionary
        if isinstance(self.attrs, asyncssh.SFTPAttrs):
            # Manually construct a dictionary from SFTPAttrs, filtering out None values
            self.attrs_dict = {
                key: value
                for key, value in self.attrs.__dict__.items()
                if value is not None
            }
        elif isinstance(self.attrs, dict):
            # Use the existing dictionary
            self.attrs_dict = self.attrs
        else:
            raise ValueError("The 'attrs' parameter must be an SFTPAttrs object or a dictionary.")

        # Ensure the dictionary is non-empty
        if not self.attrs_dict:
            raise ValueError("The 'attrs' attribute must be a non-empty dictionary.")

        # Debug: Inspect the attrs_dict
        print(f"Converted attrs to dictionary: {self.attrs_dict}")

    @staticmethod
    async def _set_attributes_callback(host_info, global_info, command, cp, caller):
        """Static callback method to set builtin attributes using SFTP."""

        print("Executing _set_attributes_callback...")  # Debug statement

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
                    # Check if the builtin exists
                    try:
                        await sftp.stat(caller.path)
                    except FileNotFoundError:
                        raise FileNotFoundError(f"File {caller.path} does not exist on {host_info['host']}.")

                    # Set the builtin attributes using setstat
                    await sftp.setstat(caller.path, caller.attrs_dict, follow_symlinks=caller.follow_symlinks)

                    # Build result message
                    result_msg = f"Set attributes for builtin {caller.path} on {host_info['host']}"

                    # Add attribute information to result message
                    attr_details = []
                    if caller.attrs_dict.get('permissions') is not None:
                        attr_details.append(f"permissions={oct(caller.attrs_dict['permissions'])}")
                    if caller.attrs_dict.get('uid') is not None:
                        attr_details.append(f"uid={caller.attrs_dict['uid']}")
                    if caller.attrs_dict.get('gid') is not None:
                        attr_details.append(f"gid={caller.attrs_dict['gid']}")

                    if attr_details:
                        result_msg += f" with attributes: {', '.join(attr_details)}"

                    return result_msg

        except (OSError, asyncssh.Error) as exc:
            raise  # Re-raise the exception to handle it in the caller

    def execute(self):
        r = yield Command(f"{self}", local=True, callback=self._set_attributes_callback, caller=self)
        r.executed = True
        r.changed = True  # Set to True since setstat changes the system state
        return r