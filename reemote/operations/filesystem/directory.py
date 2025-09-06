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

    def execute(self):
        """
        Execute the directory management logic as a generator.

        This method checks if the directory exists and performs actions based on the `present` flag.
        """
        _sudo = "sudo -S " if self.sudo else ""
        # Check whether the directory exists
        r: Result = yield f"{_sudo}[ -d {self.path} ]"
        # print(f">>>>> Received in Directory: {r}")

        # Present directory does not exist, so create it
        yield Operation(command=f"{_sudo}mkdir -p {self.path}",
                            condition=self.present and r.cp.returncode != 0,
                            changed=True)
        # print(f">>>>> Received in Directory: {r}")

        # Not Present directory exists, so remove it
        yield Operation(command=f"{_sudo}rmdir -p {self.path}",
                            condition=not self.present and r.cp.returncode == 0,
                            changed=True)
        # print(f">>>>> Received in Directory: {r}")
