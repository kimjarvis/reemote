class Mkpasswd:
    """
    A class to encapsulate the functionality of adding a user password in Unix-like operating systems.
    It allows creating a new user account with specified password and privilege options.

    Attributes:
        password (str): The password for the new user account.
        sudo (bool): If `True`, the user will be created with `sudo` privileges.
        su (bool): If `True`, the user will be created with `su` privileges.

    **Examples:**

    .. code:: python

        yield Mkpasswd(password="secret123")

    Usage:
        This class is designed to be used in a generator-based workflow where user creation
        commands are yielded for execution on remote hosts.

    Notes:
        - Privilege escalation (sudo/su) is handled based on the respective flags.
    """
    def __init__(self,
                 password: str,
                 sudo: bool = False,
                 su: bool = False):
        self.password = password
        self.sudo = sudo
        self.su = su

    def __repr__(self):
        return (f"Usermod("
                f"password={self.password!r}, "
                f"sudo={self.sudo!r}, "
                f"su={self.su!r})")

    def execute(self):
        from reemote.operations.server.shell import Shell
        r = yield Shell(f"mkpasswd -m sha-512 {self.password}", sudo=self.sudo,su=self.su)
        r.cp.stdout = r.cp.stdout.rstrip('\n')
