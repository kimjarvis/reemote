import asyncssh
from reemote.operation import Operation


class Mkdir:
    """
    A class to encapsulate the functionality of mkdir in Unix-like operating systems.

    Attributes:
        path (str): The directory path to create.

    **Examples:**

    .. code:: python

        yield Mkdir(path='/home/user/hfs')

    Usage:
        This class is designed to be used in a generator-based workflow where commands are yielded for execution.
    """

    def __init__(self, path: str):
        self.path = path

    def __repr__(self):
        return f"Mkdir(path={self.path!r})"

    @staticmethod
    async def _mkdir_callback(host_info, global_info, command, cp, caller):
        """Static callback method for directory creation"""

        # Validate host_info with proper error messages
        required_keys = ['host', 'username', 'password']
        missing_keys = []
        invalid_keys = []

        for key in required_keys:
            if key not in host_info:
                missing_keys.append(key)
            elif host_info[key] is None:
                invalid_keys.append(key)

        if missing_keys:
            raise ValueError(f"Missing required keys in host_info: {missing_keys}")
        if invalid_keys:
            raise ValueError(f"None values for keys in host_info: {invalid_keys}")

        # Validate caller attributes
        if caller.path is None:
            raise ValueError("The 'path' attribute of the caller cannot be None.")

        # Clean host_info by removing None values and keys that asyncssh doesn't expect
        clean_host_info = {
            'host': host_info['host'],
            'username': host_info['username'],
            'password': host_info['password']
        }

        # Add optional parameters only if they exist and are not None
        optional_keys = ['port', 'known_hosts', 'client_keys', 'passphrase']
        for key in optional_keys:
            if key in host_info and host_info[key] is not None:
                clean_host_info[key] = host_info[key]

        try:
            async with asyncssh.connect(**clean_host_info) as conn:
                async with conn.start_sftp_client() as sftp:
                    # Create the remote directory
                    await sftp.mkdir(caller.path)
                    return f"Successfully created directory {caller.path} on {host_info['host']}"

        except (OSError, asyncssh.Error) as exc:
            # Provide more detailed error information
            raise Exception(f"Failed to create directory {caller.path} on {host_info['host']}: {str(exc)}")

    def execute(self):
        r = yield Operation(f"{self}", local=True, callback=self._mkdir_callback, caller=self)
        r.executed = True
        # Directory creation is inherently a changing operation
        r.changed = True
        return r