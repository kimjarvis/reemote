class Add_user:
    """
    A class to encapsulate the functionality of adding a user in Unix-like operating systems.
    It allows creating a new user account with specified password and privilege options.

    Attributes:
        user (str): The username to be created.
        password (str): The password for the new user account.
        guard (bool): If `False` the user creation command will not be executed.
        sudo (bool): If `True`, the user will be created with `sudo` privileges.
        su (bool): If `True`, the user will be created with `su` privileges.

    **Examples:**

    .. code:: python

        # Create a new user with password
        yield Add_user(user="john", password="secret123")
        # Create a user with sudo privileges
        yield Add_user(user="admin", password="admin123", sudo=True)

    Usage:
        This class is designed to be used in a generator-based workflow where user creation
        commands are yielded for execution on remote hosts.

    Notes:
        - The user creation uses `adduser -D` to create a system user without a home directory.
        - The password is set using `chpasswd` for secure password assignment.
        - Privilege escalation (sudo/su) is handled based on the respective flags.
    """
    def __init__(self, user: str = None,
                 password: str = None,
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):
        self.user = user
        self.password = password
        self.guard = guard
        self.sudo = sudo
        self.su = su

    def __repr__(self):
        return (f"Add_user("
                f"user={self.user!r}, "
                f"password={self.password!r}, "
                f"guard={self.guard!r}, "
                f"sudo={self.sudo!r}, su={self.su!r})")

    def execute(self):
        from reemote.operations.server.shell import Shell
        yield Shell(f'adduser -D {self.user} && echo "{self.user}:{self.password}" | chpasswd', su=self.su)
