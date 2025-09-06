from asyncssh import SSHCompletedProcess

class Result:

    def __init__(self, cp: SSHCompletedProcess, host: str, changed: bool = False):
        self.cp = cp
        self.host = host
        self.changed = changed

