class Setup_vm:
    def __init__(self,
                 vm: str,
                 name: str,
                 user: str,
                 user_password: str,
                 root_password: str,
                 ):
        self.vm = vm
        self.name = name
        self.user = user
        self.user_password = user_password
        self.root_password = root_password

    def __repr__(self):
        return (f"Setup_vm("
                f"vm={self.vm!r}, "
                ")")

    @staticmethod
    async def _is_localhost(host_info, global_info, command, cp, caller):
        return global_info.get("localhost",False)

    def execute(self):
        from reemote.operation import Operation
        r = yield Operation(f"{self}", local=True, callback=self._is_localhost, caller=self)
        print(r)
        if r.cp.stdout:
            from reemote.operations.server.shell import Shell
            yield Shell(f"lxc init debian {self.vm}",sudo=True)
            yield Shell(f"lxc start {self.vm}",sudo=True)
            yield Shell(f"lxc exec {self.vm} -- apt-get update",sudo=True)
            yield Shell(f"lxc exec {self.vm} -- apt install -y openssh-server",sudo=True)
            yield Shell(f"lxc exec {self.vm} -- systemctl start ssh",sudo=True)
            yield Shell(f"lxc exec {self.vm} -- systemctl enable ssh",sudo=True)
            yield Shell(f"lxc exec {self.vm} -- systemctl status ssh",sudo=True)
            yield Shell(f"lxc exec {self.vm} -- useradd -m -s /bin/bash -c '{self.name}' {self.user}",sudo=True)
            r0=yield Shell(f"mkpasswd -m sha-512 {self.user_password}")
            print(r0.cp.stdout)
            r1=yield Shell(f"mkpasswd -m sha-512 {self.root_password}")
            print(r1.cp.stdout)
            yield Shell(f"lxc exec {self.vm} -- usermod --password '{r0.cp.stdout}' {self.user}",sudo=True)
            yield Shell(f"lxc exec {self.vm} -- usermod --password '{r1.cp.stdout}' root",sudo=True)
        else:
            from reemote.operations.server.shell import Shell
            from reemote.operations.sftp.write_file import Write_file
            from reemote.operations.sftp.chmod import Chmod
            from reemote.operations.sftp.remove import Remove
            yield Remove(
                path=f'/tmp/{self.user}',
            )
            yield Remove(
                path='/tmp/set_owner.sh',
            )
            yield Write_file(path=f'/tmp/{self.user}', text=f'{self.user} ALL=(ALL:ALL) ALL')
            yield Write_file(path='/tmp/set_owner.sh',
                             text=f'chown root:root /tmp/{self.user};cp /tmp/{self.user} /etc/sudoers.d')
            yield Chmod(
                path='/tmp/set_owner.sh',
                mode=0o755,
            )
            yield Shell("bash /tmp/set_owner.sh", su=self.su)