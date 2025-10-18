# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
class Get_ip:
    """
    Returns the ip address of the lxc container from the local host.

    Attributes:

    **Examples:**

    .. code:: python

        yield Get_ip()

    """

    def __init__(self,
                 vm: str,
                 sudo: bool = False,
                 su: bool = False):
        self.vm = vm
        self.sudo: bool = sudo
        self.su: bool = su

    def __repr__(self) -> str:
        return (f"Get_ip("
                f"vm={self.vm!r}, "
                f"sudo={self.sudo!r}, "
                f"su={self.su!r}"
                f")")

    def execute(self):
        from reemote.operations.server.shell import Shell
        r = yield Shell(f"""sudo lxc info {self.vm}"""+"""| grep "inet:" | grep "global" | awk '{print $2}' | cut -d'/' -f1""",sudo=self.sudo, su=self.su)
        r.cp.stdout = r.cp.stdout.rstrip('\n')
