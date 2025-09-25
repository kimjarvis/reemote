class Add_sudo_user:
    """
    A class to encapsulate the functionality of adding a user with sudo privileges
    in Unix-like operating systems. It creates a new user account and configures
    sudo access by adding the user to the sudoers system.

    Attributes:
        user (str): The username to be created with sudo privileges.
        password (str): The password for the new user account.
        guard (bool): If `False` the user creation and sudo configuration will not be executed.
        sudo (bool): If `True`, the operations will be executed with `sudo` privileges.
        su (bool): If `True`, the operations will be executed with `su` privileges.

    **Examples:**

    .. code:: python

        # Create a new user with sudo privileges
        yield Add_sudo_user(user="admin", password="securepass123")
        # Create a sudo user with specific privilege escalation
        yield Add_sudo_user(user="deployer", password="deploy123", sudo=True)

    Usage:
        This class is designed to be used in a generator-based workflow where user creation
        and sudo configuration commands are yielded for execution on remote hosts.

    Notes:
        - The user creation uses `adduser -D` to create a system user without a home directory.
        - The password is set using `chpasswd` for secure password assignment.
        - Sudo privileges are configured by creating a file in `/etc/sudoers.d/` directory.
        - The operation includes cleanup of temporary files used during configuration.
        - The sudo configuration grants the user full sudo privileges (`ALL=(ALL:ALL) ALL`).
    """

    def __init__(self, user: str=None,
                 password: str=None,
                 guard: bool = True,
                 sudo: bool = False,
                 su: bool = False):
        self.user = user
        self.password = password
        self.guard = guard
        self.sudo = sudo
        self.su = su

    def __repr__(self):
        return (f"Add_sudo_user("
                f"user={self.user!r}, "
                f"password={self.password!r}, "
                f"guard={self.guard!r}, "
                f"sudo={self.sudo!r}, su={self.su!r})")

    def execute(self):
        if self.guard:
            from reemote.operations.server.shell import Shell
            from reemote.operations.sftp.write_file import Write_file
            from reemote.operations.sftp.chmod import Chmod
            from reemote.operations.sftp.remove import Remove
            yield Shell(f'adduser -D {self.user} && echo "{self.user}:{self.password}" | chpasswd', su=self.su)
            yield Remove(
                path=f'/tmp/{self.user}',
            )
            yield Remove(
                path='/tmp/set_owner.sh',
            )
            yield Write_file(path=f'/tmp/{self.user}', text=f'{self.user} ALL=(ALL:ALL) ALL')
            yield Write_file(path='/tmp/set_owner.sh', text=f'chown root:root /tmp/{self.user};cp /tmp/{self.user} /etc/sudoers.d')
            yield Chmod(
                path='/tmp/set_owner.sh',
                mode=0o755,
            )
            yield Shell("bash /tmp/set_owner.sh", su=self.su)
