class Operation:

    def __init__(self, command: str, changed: bool = False):
        self.command = command
        self.host_info = None
        self.sudo_info = None

