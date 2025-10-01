from reemote.operation import Operation
class Init:
    """
    A class to manage lxc operations from the local host.

    Attributes:
        vm (str): Name of the virtual machine
        sudo (bool): If `True`, the commands will be executed with `sudo` privileges.
        su (bool): If `True`, the commands will be executed with `su` privileges.

    **Examples:**

    .. code:: python

        yield Init(vm="debian-vm10")

    Usage:
        Initialise container

    Notes:
        - Commands are constructed based on the `present`, `sudo`, and `su` flags.
        - The `changed` flag is set if the package state changes after execution.
    """

    def __init__(self,
                 vm: str,
                 image: str,
                 user: str,
                 user_password: str,
                 options: str = "",
                 sudo: bool = False,
                 su: bool = False):
        self.vm = vm
        self.image = image
        self.user = user
        self.user_password = user_password
        self.options = options
        self.sudo: bool = sudo
        self.su: bool = su

    def __repr__(self) -> str:
        return (f"Init("
                f"vm={self.vm!r}, "
                f"image={self.image!r}, "
                f"user={self.user!r}, "
                f"user_password={self.user_password!r}, "
                f"options={self.options!r}, "
                f"sudo={self.sudo!r}, "
                f"su={self.su!r}"
                f")")

    def execute(self):
        from reemote.operations.server.shell import Shell
        # yield Shell(f"""lxc init {self.image} {self.vm} --config=user.user-data="
        #               users:
        #               - name: {self.user}
        #                 passwd: $(mkpasswd -m sha-512 "{self.user_password}")
        #             "
        #             """
        #             ,sudo=self.sudo,su=self.su)

        yield Shell(f"""lxc init {self.image} {self.vm} {self.options}
                    """
                    ,sudo=self.sudo,su=self.su)
