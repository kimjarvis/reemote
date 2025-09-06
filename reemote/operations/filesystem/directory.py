from reemote.operation import Operation
from reemote.result import Result

class Directory:
    def __init__(self, path: str, present: bool, sudo: bool = False):
        """
        Initialize the Directory class with the required parameters.

        :param path: The directory path to check or modify.
        :param present: Whether the directory should exist (True) or not (False).
        :param sudo: Whether to use sudo privileges (default is False).
        """
        self.path = path
        self.present = present
        self.sudo = sudo

    def __repr__(self):
        """
        Return an unambiguous string representation of the Directory object.
        This representation can be used to recreate the object.
        """
        return f"Directory(path={self.path!r}, present={self.present!r}, sudo={self.sudo!r})"

    def execute(self):
        yield f"echo {self}"
        """
        Execute the directory management logic as a generator.

        This method checks if the directory exists and performs actions based on the `present` flag.
        """
        _sudo = "sudo -S " if self.sudo else ""
        # Check whether the directory exists
        r0: Result = yield f"{_sudo}[ -d {self.path} ]"
        # print(f">>>>> Received in Directory: {r}")

        if self.present and r0.cp.returncode != 0:
            # Present directory does not exist, so create it
            r1 =yield f"{_sudo}mkdir -p {self.path}"
            # print(f">>>>> Received in Directory: {r1}")
            r1.changed=True

        if not self.present and r0.cp.returncode == 0:
            # Not Present directory exists, so remove it
            r2 = yield f"{_sudo}rmdir -p {self.path}"
            # print(f">>>>> Received in Directory: {r2}")
            r2.changed=True
