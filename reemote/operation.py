class Operation:

    def __init__(self, command: str, condition: bool = True, changed: bool = False):
        self.command = command
        self.condition = condition
        self.changed = changed
        self.host_info = None
        self.sudo_info = None

    def __str__(self):
        """
        Return a string representation of the object.
        """
        return f"Operation(command={self.command},condition={self.condition},changed={self.changed},host_info={self.host_info},sudo_info={self.sudo_info})"

    def __repr__(self):
        """
        Return the official string representation of the object.
        """
        return self.__str__()
