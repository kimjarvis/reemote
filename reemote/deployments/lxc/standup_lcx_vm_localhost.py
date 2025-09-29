from ipaddress import ip_address

from dotenv import set_key
import time

class Standup_lcx_vm_localhost:
    def __init__(self,
                 vm: str,
                 image: str,
                 name: str,
                 user: str,
                 user_password: str,
                 root_password: str,
                 sudo: bool = False,
                 su: bool = False):
        self.vm = vm
        self.image = image
        self.name = name
        self.user = user
        self.user_password = user_password
        self.root_password = root_password
        self.sudo: bool = sudo
        self.su: bool = su

    def __repr__(self) -> str:
        return (f"Debian("
                f"sudo={self.sudo!r}, "
                f"su={self.su!r}"
                f")")

    def execute(self):
        from reemote.operations.server.shell import Shell
        from reemote.operations.lxc.copy import Copy
        from reemote.operations.lxc.init import Init
        from reemote.operations.lxc.start import Start
        from reemote.facts.lxc.get_status import Get_status
        from reemote.operations.lxc.exec import Exec
        from reemote.operations.users.mkpasswd import Mkpasswd
        from reemote.facts.lxc.get_ip import Get_ip
        from reemote.utilities.add_localhost_to_known_hosts import add_localhost_to_known_hosts
        from reemote.utilities.make_inventory import make_inventory
        # Call the function
        add_localhost_to_known_hosts()
        yield Copy(image=self.image,,sudo=self.sudo,su=self.su)
        yield Init(image=self.image,
            vm=self.vm,
            user=self.user,
            user_password=self.user_password,
            sudo=self.sudo,
            su=self.su,
        )
        r = yield Start(vm=self.vm)
        while r.cp.stdout!="RUNNING":
            r=yield Get_status(vm=self.vm)
            print(r)
            time.sleep(1)
        if "debian" in self.image or "ubuntu" in self.image:
            yield Exec(vm=self.vm,cmd='apt-get update',sudo=self.sudo,su=self.su)
            yield Exec(vm=self.vm,cmd='apt install -y openssh-server',sudo=self.sudo,su=self.su)
            yield Exec(vm=self.vm, cmd='systemctl start ssh', sudo=self.sudo, su=self.su)
            yield Exec(vm=self.vm, cmd='systemctl enable ssh', sudo=self.sudo, su=self.su)
            yield Exec(vm=self.vm, cmd='systemctl status ssh', sudo=self.sudo, su=self.su)
            yield Exec(vm=self.vm,cmd=f"useradd -m -s /bin/bash -c '{self.name}' {self.user}", sudo=self.sudo,su=self.su)
            r=yield Mkpasswd(password=self.user_password)
            print(r)
            yield Exec(vm=self.vm,cmd=f"usermod --password '{r.cp.stdout}' {self.user}", sudo=self.sudo,su=self.su)
            # Should check whether there are pipes in these commands as cannot run in terminal ?  We can grep the output but not run a script like thing in lxc
            # yield Exec(vm=self.vm,cmd=f"echo '{self.user} ALL=(ALL:ALL) ALL' | sudo tee /etc/sudoers.d/{self.user} > /dev/null", sudo=self.sudo,su=self.su)
            yield Exec(vm=self.vm, cmd=f"""bash -c 'cat <<EOF > /etc/sudoers.d/{self.user}
{self.user} ALL=(ALL:ALL) ALL
EOF'
""")
            yield Exec(vm=self.vm,cmd=f"sudo chmod 440 /etc/sudoers.d/{self.user}", sudo=self.sudo,su=self.su)
        if "alpine" in self.image:
            yield Exec(vm=self.vm,cmd='apk update',sudo=self.sudo,su=self.su)
            # yield Exec(vm=self.vm,cmd='apk add shadow',sudo=self.sudo,su=self.su)
            yield Exec(vm=self.vm,cmd='apk add openssh-server',sudo=self.sudo,su=self.su)
            yield Exec(vm=self.vm, cmd='rc-update add sshd', sudo=self.sudo, su=self.su)
            yield Exec(vm=self.vm, cmd='rc-service sshd start', sudo=self.sudo, su=self.su)
            yield Exec(vm=self.vm, cmd='rc-service sshd status', sudo=self.sudo, su=self.su)
            yield Exec(vm=self.vm, cmd=f"useradd -m -s /bin/sh -c '{self.name}' {self.user}", sudo=self.sudo,su=self.su)
            r = yield Mkpasswd(password=self.user_password)
            print(r)
            yield Exec(vm=self.vm, cmd=f"usermod --password '{r.cp.stdout}' {self.user}", sudo=self.sudo, su=self.su)
            yield Exec(vm=self.vm,cmd='apk add sudo',sudo=self.sudo,su=self.su)
            yield Exec(vm=self.vm, cmd=f"""sh -c 'echo "{self.user} ALL=(ALL:ALL) ALL" > /etc/sudoers.d/{self.user}' """, sudo=self.sudo, su=self.su)

        #     # No rc-service or shadow on debian
        #     yield Exec(vm=self.vm, cmd=f"""sh -c "echo -e 'PasswordAuthentication yes\nPermitRootLogin no\nAllowUsers {self.user}' >> /etc/ssh/sshd_config && rc-service sshd restart" """, sudo=self.sudo,su=self.su)
        #     r=yield Mkpasswd(password=self.user_password)
        #     print(r.cp.stdout)
        #     yield Exec(vm=self.vm, cmd=f"""sh -c 'echo "{self.user}:{r.cp.stdout}" >> /etc/shadow'""", sudo=self.sudo,su=self.su)
        #
        # # yield Shell(f"lxc exec {self.vm} -- usermod --password '{r.cp.stdout}' {self.user}", sudo=self.sudo,su=self.su)
        # # r=yield Mkpasswd(password=self.root_password)
        # # yield Shell(f"lxc exec {self.vm} -- usermod --password '{r.cp.stdout}' root", sudo=self.sudo,su=self.su)

        r=yield Get_ip(vm=self.vm, sudo=self.sudo,su=self.su)
        ip_address=r.cp.stdout
        yield Shell(f"ssh-keyscan {ip_address} >> ~/.ssh/known_hosts", sudo=self.sudo,su=self.su)

        make_inventory(
            inventory_filename=f"inventory-{self.vm}.py",
            image=self.image,
            vm=self.vm,
            name=self.name,
            user=self.user,
            user_password=self.user_password,
            root_password=self.root_password,
            ip_address=ip_address
        )

        # Write the output to the console
        output = f"""{self.image} virtual machine {self.vm} started
view credentials at http://{ip_address}
ssh access:
ssh {self.user}@{ip_address}
using password: {self.user_password}
the user {self.name} {self.user} has been added to the sudoers file
wrote inventory file inventory-{self.vm}.py"""

        print(output)
