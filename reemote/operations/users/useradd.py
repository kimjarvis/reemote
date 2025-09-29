class Useradd:
    """
    A class to encapsulate the functionality of adding a user in Unix-like operating systems.
    It allows creating a new user account with specified password and privilege options.

    Attributes:
        user (str): The username to be created.
        sudo (bool): If `True`, the user will be created with `sudo` privileges.
        su (bool): If `True`, the user will be created with `su` privileges.

    **Examples:**

    .. code:: python

        yield Useradd(user="john",name="John Smith")

    Usage:
        This class is designed to be used in a generator-based workflow where user creation
        commands are yielded for execution on remote hosts.

    Notes:
        - Privilege escalation (sudo/su) is handled based on the respective flags.
    """
    def __init__(self,
                 user: str = None,
                 name: str = None,
                 sudo: bool = False,
                 su: bool = False):
        self.user = user
        self.name = name
        self.sudo = sudo
        self.su = su

    def __repr__(self):
        return (f"Add_user("
                f"name={self.name!r}, "
                f"user={self.user!r}, "
                f"sudo={self.sudo!r}, "
                f"su={self.su!r})")

    def execute(self):
        from reemote.operations.server.shell import Shell
        yield Shell(f"useradd -m -s /bin/bash -c '{self.name}' {self.user}", sudo=self.sudo,su=self.su)
