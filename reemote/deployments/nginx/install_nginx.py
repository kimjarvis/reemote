# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
from ipaddress import ip_address


class Install_nginx:
    def __init__(self,
                 body: str,
                 title: str,
                 # vm: str,
                 # image: str,
                 # name: str,
                 user: str,
                 # user_password: str,
                 # root_password: str,
                 # ip_address: str,
                 sudo: bool = False,
                 su: bool = False):
        self.body = body
        self.title = title
        # self.vm = vm
        # self.image = image
        # self.name = name
        self.user = user
        # self.user_password = user_password
        # self.root_password = root_password
        # self.ip_address = ip_address
        self.sudo: bool = sudo
        self.su: bool = su

    def __repr__(self) -> str:
        return (f"Install_nginx("
                f"title={self.title!r}, "
                f"body={self.body!r}, "
                f"user={self.user!r}, "
                f"sudo={self.sudo!r}, "
                f"su={self.su!r}"
                f")")

    def execute(self):
        from reemote.operations.server.shell import Shell
        from reemote.operations.sftp.write_file import Write_file
        from reemote.operations.sftp.chmod import Chmod
        from reemote.operations.filesystem.chown import Chown
        from reemote.facts.server.get_os import Get_OS
        from reemote.operations.sftp.touch import Touch
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

        r = yield Get_OS()
        os = r.cp.stdout
        print(f"installing nginx on {os}")
        if "Alpine" in os:
            from reemote.operations.apk.update import Update
            from reemote.operations.apk.upgrade import Upgrade
            from reemote.operations.apk.packages import Operation_packages
            yield Update(sudo=True)
            yield Upgrade(sudo=True)
            yield Operation_packages(packages=["nginx", "ufw", "iptables"], present=True, sudo=True)
            yield Shell("iptables -A INPUT -p tcp --dport 80 -j ACCEPT", sudo=True)
            yield Shell("iptables -A INPUT -p tcp --dport 443 -j ACCEPT", sudo=True)
            yield Shell("rc-update add iptables", sudo=True)
            yield Shell("rc-service nginx start", sudo=True)
            yield Shell("rc-service nginx status", sudo=True)
            yield Shell("rc-update add nginx default", sudo=True)
            yield Shell("service iptables save", sudo=True)
            yield Shell("ufw allow 80", sudo=True)
            yield Shell("ufw allow 443", sudo=True)
            yield Shell("ufw enable", sudo=True)
        if "Debian" in os or "Ubuntu" in os:
            from reemote.operations.apt.update import Update
            from reemote.operations.apt.upgrade import Upgrade
            from reemote.operations.apt.packages import Operation_packages
            yield Update(sudo=True)
            yield Upgrade(sudo=True)
            yield Operation_packages(packages=["nginx", "ufw"], present=True, sudo=True)
            yield Shell("ufw allow 'Nginx Full'", sudo=True)
        if "CentOS" in os:
            from reemote.operations.dnf.update import Update
            from reemote.operations.dnf.upgrade import Upgrade
            from reemote.operations.dnf.packages import Operation_packages
            yield Update(sudo=True)
            yield Upgrade(sudo=True)
            yield Operation_packages(packages=["nginx", "ufw"], present=True, sudo=True)
            yield Shell("ufw allow 'Nginx Full'", sudo=True)
        if "Arch" in os:
            from reemote.operations.pacman.update import Update
            from reemote.operations.pacman.packages import Operation_packages
            yield Update(sudo=True)
            yield Operation_packages(packages=["nginx", "ufw"], present=True, sudo=True)
            yield Shell("ufw allow 'Nginx Full'", sudo=True)
        if "SUSE" in os:
            from reemote.operations.zypper.update import Update
            from reemote.operations.zypper.packages import Operation_packages
            yield Update(sudo=True)
            yield Operation_packages(packages=["nginx", "ufw"], present=True, sudo=True)
            yield Shell("ufw allow 'Nginx Full'", sudo=True)


        if "Alpine" in os:
            index_directory = "/usr/share/nginx/html"
            index_file = "index.html"
        if "Debian" in os or "Ubuntu" in os:
            index_directory="/var/www/html"
            index_file="index.nginx-debian.html"
        if "CentOS" in os:
            index_directory="/usr/share/nginx/html"
            index_file="index.html"
        if "Arch" in os:
            index_directory="/usr/share/nginx/html"
            index_file="index.html"
        if "SUSE" in os:
            index_directory="/usr/share/nginx/html"
            index_file="index.html"

        yield Chown(path=index_directory,owner=self.user, group=self.user, sudo=True)
        yield Chmod(
            path=index_directory,
            mode=0o755,
        )
        yield Chown(path=f"{index_directory}/{index_file}", owner=self.user, group=self.user, sudo=True)
        yield Chmod(
            path=f"{index_directory}/{index_file}",
            mode=0o755,
        )
        yield Write_file(path=f"{index_directory}/{index_file}",
                         text=f"""<!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>{self.title}</title>
            </head>
            <body>
                {self.body}
            </body>
            </html>
        """)

        if "Alpine" in os:
            yield Chown(path="/etc/nginx/http.d/default.conf", owner=self.user, group=self.user, sudo=True)
            yield Write_file(path=f"/etc/nginx/http.d/default.conf",
                             text="""
server {
    listen       80;
    server_name  localhost;

    root /usr/share/nginx/html;
    index index.html;
}                             
            """)
        if "CentOS" in os:
            # yield Chown(path="/etc/nginx/default.d", owner=self.user, group=self.user, sudo=True)
            # yield Chmod(
            #     path="/etc/nginx/default.d",
            #     mode=0o755,
            # )
            yield Touch(path="/etc/nginx/conf.d/centos.conf")
            yield Chown(path="/etc/nginx/conf.d/centos.conf", owner=self.user, group=self.user, sudo=True)
            yield Chmod(
                path=f"/etc/nginx/conf.d/centos.conf",
                mode=0o755,
            )
            yield Write_file(path=f"/etc/nginx/conf.d/centos.conf",
                             text="""
server {
    listen       80;
    server_name  localhost;

    root /usr/share/nginx/html;
    index index.html;
}                             
                        """)
            if "CentOS" in os:
                # yield Chown(path="/etc/nginx/conf.d", owner=self.user, group=self.user, sudo=True)
                # yield Chmod(
                #     path="/etc/nginx/conf.d",
                #     mode=0o755,
                # )
                yield Touch(path="/etc/nginx/conf.d/centos.conf")
                yield Chown(path="/etc/nginx/conf.d/centos.conf", owner=self.user, group=self.user, sudo=True)
                yield Chmod(
                    path=f"/etc/nginx/conf.d/centos.conf",
                    mode=0o755,
                )
                yield Write_file(path=f"/etc/nginx/conf.d/centos.conf",
                                 text="""
        server {
            listen       80;
            server_name  localhost;

            root /usr/share/nginx/html;
            index index.html;
        }                             
                                """)


            if "SUSE" in os:
                yield Touch(path="/etc/nginx/conf.d/centos.conf")
                yield Chown(path="/etc/nginx/conf.d/centos.conf", owner=self.user, group=self.user, sudo=True)
                yield Chmod(
                    path=f"/etc/nginx/conf.d/centos.conf",
                    mode=0o755,
                )
                yield Write_file(path=f"/etc/nginx/conf.d/centos.conf",
                                 text="""
        server {
            listen       80;
            server_name  localhost;

            root /usr/share/nginx/html;
            index index.html;
        }                             
                                """)

        if "Alpine" in os:
            yield Shell("rc-service nginx restart", sudo=True)
            print("restart nginx")
        if "CentOS" in os or "Arch" in os or "Debian" in os or "Ubuntu" in os or "SUSE" in os:
            yield Shell("systemctl restart nginx", sudo=True)
