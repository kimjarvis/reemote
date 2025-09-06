from asyncssh import SSHCompletedProcess

class Result:

    def __init__(self, cp: SSHCompletedProcess, host: str, executed: bool, changed: bool = False):
        self.cp = cp
        self.host = host
        self.executed = executed
        self.changed = changed

    def __str__(self):
        """
        Return a string representation of the object, including stdout and returncode.
        """
        stdout = self.cp.stdout.strip() if self.cp.stdout else "<no stdout>"
        returncode = self.cp.returncode
        host = self.host
        return f"Result(host={host},stdout={stdout}, returncode={returncode},changed={self.changed})"

    def __repr__(self):
        """
        Return the official string representation of the object.
        """
        return self.__str__()
