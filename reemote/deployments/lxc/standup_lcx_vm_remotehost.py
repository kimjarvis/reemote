from ipaddress import ip_address


class Standup_lcx_vm_remotehost:
    def __init__(self,
                 vm: str,
                 image: str,
                 name: str,
                 user: str,
                 user_password: str,
                 root_password: str,
                 ip_address: str,
                 sudo: bool = False,
                 su: bool = False):
        self.vm = vm
        self.image = image
        self.name = name
        self.user = user
        self.user_password = user_password
        self.root_password = root_password
        self.ip_address = ip_address
        self.sudo: bool = sudo
        self.su: bool = su

    def __repr__(self) -> str:
        return (f"Debian("
                f"sudo={self.sudo!r}, "
                f"su={self.su!r}"
                f")")

    def execute(self):
        from reemote.operations.server.shell import Shell
        from reemote.operations.sftp.write_file import Write_file
        from reemote.operations.sftp.chmod import Chmod
        from reemote.operations.filesystem.chown import Chown
        from reemote.operations.sftp.remove import Remove
        # yield Remove(
        #     path=f'/tmp/{self.user}',
        # )
        # yield Remove(
        #     path='/tmp/set_owner.sh',
        # )
        # yield Write_file(path=f'/tmp/{self.user}', text=f'{self.user} ALL=(ALL:ALL) ALL')
        # yield Write_file(path='/tmp/set_owner.sh',
        #                  text=f'chown root:root /tmp/{self.user};cp /tmp/{self.user} /etc/sudoers.d')
        # yield Chmod(
        #     path='/tmp/set_owner.sh',
        #     mode=0o755,
        # )
        # yield Shell("bash /tmp/set_owner.sh", su=True)

#         if "alpine" in self.image:
#             from reemote.operations.apk.update import Update
#             from reemote.operations.apk.upgrade import Upgrade
#             from reemote.operations.apk.packages import Packages
#             yield Update(sudo=True)
#             yield Upgrade(sudo=True)
#             yield Packages(packages=["nginx", "ufw"], present=True, sudo=True)
#         if "debian" in self.image or "ubuntu" in self.image:
#             from reemote.operations.apt.update import Update
#             from reemote.operations.apt.upgrade import Upgrade
#             from reemote.operations.apt.packages import Packages
#             yield Update(sudo=True)
#             yield Upgrade(sudo=True)
#             yield Packages(packages=["nginx", "ufw"], present=True, sudo=True)
#         if "centos" in self.image:
#             from reemote.operations.dnf.update import Update
#             from reemote.operations.dnf.upgrade import Upgrade
#             from reemote.operations.dnf.packages import Packages
#             yield Update(sudo=True)
#             yield Upgrade(sudo=True)
#             yield Packages(packages=["nginx", "ufw"], present=True, sudo=True)
#         yield Shell("ufw allow 'Nginx Full'", sudo=True)
#         if "centos" in self.image:
#             yield Chown(path='/usr/share/nginx/html', owner="kim", group="kim")
#             yield Chown(path='/usr/share/nginx/html/index.html', owner="kim", group="kim")
#             yield Chmod(
#                 path='/usr/share/nginx/html',
#                 mode=0o755,
#             )
#             yield Chmod(
#                 path='/usr/share/nginx/html/index.html',
#                 mode=0o755,
#             )
#             yield Write_file(path='/usr/share/nginx/html/index.html',
#                              text=f"""<!DOCTYPE html>
#         <html lang="en">
#         <head>
#             <meta charset="UTF-8">
#             <meta name="viewport" content="width=device-width, initial-scale=1.0">
#             <title> {self.vm}</title>
#         </head>
#         <body>
#             <h1>{self.image} virtual machine {self.vm} started</h1>
#             <p>ssh access:</p>
#             <p>ssh {self.user}@{ip_address}</p>
#             <p>using password: {self.user_password}</p>
#             <p>The user {self.user} has been added to the sudoers file</p>
#             <p>Wrote inventory file inventory-{self.vm}.py</p>
#         </body>
#         </html>
#         """)
#         else:
#             yield Chown(path='/var/www/html/', owner="kim", group="kim")
#             yield Chmod(
#                 path='/var/www/html/',
#                 mode=0o755,
#             )
#             yield Write_file(path='/var/www/html/index.html',
#                              text=f"""<!DOCTYPE html>
#         <html lang="en">
#         <head>
#             <meta charset="UTF-8">
#             <meta name="viewport" content="width=device-width, initial-scale=1.0">
#             <title> {self.vm}</title>
#         </head>
#         <body>
#             <h1>{self.image} virtual machine {self.vm} started</h1>
#             <p>ssh access:</p>
#             <p>ssh {self.user}@{ip_address}</p>
#             <p>using password: {self.user_password}</p>
#             <p>The user {self.user} has been added to the sudoers file</p>
#             <p>Wrote inventory file inventory-{self.vm}.py</p>
#         </body>
#         </html>
#         """)
#         yield Shell("systemctl restart nginx", sudo=True)
#
