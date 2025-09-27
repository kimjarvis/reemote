#!/usr/bin/env python3
"""
Stand up a virtual machine with specified configuration.

This script creates and configures a virtual machine based on the provided parameters.
All parameters are required and can be abbreviated.
"""

import argparse
import sys
import os.path
import asyncio
from reemote.execute import execute
from reemote.utilities.produce_json import produce_json
from reemote.utilities.convert_to_df import convert_to_df
from reemote.utilities.convert_to_tabulate import convert_to_tabulate
from reemote.utilities.read_inventory import read_inventory

def validate_file_path(file_path):
    """Validate that the given file path exists."""
    # Expand user home directory (~)
    expanded_path = os.path.expanduser(file_path)
    if not os.path.isfile(expanded_path):
        raise argparse.ArgumentTypeError(f"File not found: '{file_path}' (expanded to: '{expanded_path}')")
    return expanded_path


async def main():
    parser = argparse.ArgumentParser(
        description="Stand up a virtual machine with specified configuration.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example usage:
  python3 examples/standupvm/standupvm.py \\
    --image debian \\
    --vm debian-vm11 \\
    --name "Kim Jarvis" \\
    --user kim \\
    --user_password "passwd" \\
    --root_password "secret" \\
    --inventory ~/localhost.py
        """
    )

    # Add arguments with abbreviations allowed (using metavar for clarity in help)
    parser.add_argument(
        '--image',
        required=True,
        help='Base image for the virtual machine (e.g., debian, ubuntu, centos)'
    )

    parser.add_argument(
        '--vm',
        required=True,
        help='Virtual machine identifier/name (e.g., debian-vm11)'
    )

    parser.add_argument(
        '--name',
        required=True,
        help='Full name of the user (e.g., "Kim Jarvis")'
    )

    parser.add_argument(
        '--user',
        required=True,
        help='Username for SSH access (e.g., kim)'
    )

    parser.add_argument(
        '--user_password',
        required=True,
        help='Password for the user account (e.g., passwd)'
    )

    parser.add_argument(
        '--root_password',
        required=True,
        help='Root password for the system (e.g., secret)'
    )

    parser.add_argument(
        '--inventory',
        required=True,
        type=validate_file_path,
        help='Path to inventory file (e.g., ~/localhost.py). The file must exist.'
    )

    # Parse arguments
    args = parser.parse_args()


    # Read the inventory file
    inventory_func=read_inventory(args.inventory)


    class Setup_vm:
        def __init__(self,
                     vm: str,
                     image: str,
                     name: str,
                     user: str,
                     user_password: str,
                     root_password: str,
                     ):
            self.vm = vm
            self.image = image
            self.name = name
            self.user = user
            self.user_password = user_password
            self.root_password = root_password

        def __repr__(self):
            return (f"Setup_vm("
                    f"vm={self.vm!r}, "
                    f"immage={self.image!r}, "
                    f"name={self.name!r}, "
                    f"user={self.user!r}, "
                    f"user_password={self.user_password!r}, "
                    f"root_password={self.root_password!r})"
                    ")")


        def execute(self):
            from reemote.operations.server.shell import Shell
            from reemote.operations.sftp.mkdir import Mkdir
            yield Shell(f"lxc init {self.image} {self.vm}", sudo=True)
            yield Shell(f"lxc start {self.vm}", sudo=True)


            if "alpine" in self.image:
                yield Shell(f"lxc exec {self.vm} -- apk update", sudo=True)
                yield Shell(f"lxc exec {self.vm} -- apk add -y openssh-server", sudo=True)
            if "debian" in self.image or "ubuntu" in self.image:
                yield Shell(f"lxc exec {self.vm} -- apt-get update", sudo=True)
                yield Shell(f"lxc exec {self.vm} -- apt install -y openssh-server", sudo=True)
            if "centos" in self.image:
                yield Shell(f"lxc exec {self.vm} -- dnf update", sudo=True)
                yield Shell(f"lxc exec {self.vm} -- dnf install -y openssh-server", sudo=True)

            if "centos" in self.image:
                yield Shell(f"lxc exec {self.vm} -- systemctl start sshd", sudo=True)
                yield Shell(f"lxc exec {self.vm} -- systemctl enable sshd", sudo=True)
                yield Shell(f"lxc exec {self.vm} -- systemctl status sshd", sudo=True)
            else:
                yield Shell(f"lxc exec {self.vm} -- systemctl start ssh", sudo=True)
                yield Shell(f"lxc exec {self.vm} -- systemctl enable ssh", sudo=True)
                yield Shell(f"lxc exec {self.vm} -- systemctl status ssh", sudo=True)

            yield Shell(f"lxc exec {self.vm} -- useradd -m -s /bin/bash -c '{self.name}' {self.user}", sudo=True)
            r0 = yield Shell(f"mkpasswd -m sha-512 {self.user_password}")
            user_password=r0.cp.stdout.rstrip('\n')
            yield Shell(f"lxc exec {self.vm} -- usermod --password '{user_password}' {self.user}", sudo=True)
            # if "ubuntu" in self.image:
            #     # yield Shell(f"rm -rf /home/{self.user}/.ssh")
            #     # yield Shell(f"mkdir /home/{self.user}/.ssh")
            #     # yield Mkdir(f"/home/{self.user}/.ssh")
            #     pass
            if "centos" in self.image or "ubuntu" in self.image:
                yield Shell(f"lxc exec {self.vm} -- echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKEUQy84O10r+TapITpKH6Hc/C1wUcA2UzIGeWq1I7QP kim.jarvis@tpfsystems.com' >> /home/{self.user}/.ssh/authorized_keys")
            r1 = yield Shell(f"mkpasswd -m sha-512 {self.root_password}")
            root_password=r1.cp.stdout.rstrip('\n')
            yield Shell(f"lxc exec {self.vm} -- usermod --password '{root_password}' root", sudo=True)

            if "centos" in self.image or "alpine" in self.image or "ubuntu" in self.image:
                r2 = yield Shell(f"sudo lxc exec {self.vm} -- ip -4 addr show eth0 | grep -oP '(?<=inet\s)\d+(\.\d+)"+"{3}'", sudo=True)
            if "debian" in self.image:
                r2 = yield Shell(f"sudo lxc exec {self.vm} -- ip -4 addr show eth0 | grep -oP '(?<=inet\s)\d+(\.\d+){3}'", sudo=True)
            print(self.image)
            global ip_address
            ip_address = r2.cp.stdout.rstrip('\n')

    responses = await execute(inventory_func(), Setup_vm(
        vm=args.vm,
        image=args.image,
        name=args.name,
        user=args.user,
        user_password=args.user_password,
        root_password=args.root_password,
    ))
    json = produce_json(responses)
    print(json)
    df = convert_to_df(json, columns=["command", "host", "returncode", "stdout", "stderr", "error"])
    table = convert_to_tabulate(df)
    # print(table)

    print(ip_address)




    # Write the inventory file
    # Create the inventory file content
    inventory_content = f"""from typing import List, Tuple, Dict, Any
def inventory() -> List[Tuple[Dict[str, Any], Dict[str, str]]]:
    return [
        (
            {{
                'host': '{ip_address}',  # Debian
                'username': '{args.user}',  # User name
                'password': '{args.user_password}',  # Password
            }},
            {{
                'sudo_user': '{args.user}',  # Sudo user
                'sudo_password': '{args.user_password}',  # Password
                'su_user': 'root',  # su user
                'su_password': '{args.root_password}'  # su Password

            }}
        )

    ]
"""

    # Write the inventory file
    inventory_filename = f"inventory-{args.vm}.py"
    try:
        with open(inventory_filename, 'w') as f:
            f.write(inventory_content)
    except IOError as e:
        print(f"Error writing inventory file '{inventory_filename}': {e}", file=sys.stderr)
        sys.exit(1)






    class Setup_sudo_access:
        def __init__(self,
                     vm: str,
                     image: str,
                     name: str,
                     user: str,
                     user_password: str,
                     root_password: str,
                     ):
            self.vm = vm
            self.image = image
            self.name = name
            self.user = user
            self.user_password = user_password
            self.root_password = root_password


        def __repr__(self):
            return (f"Setup_vm("
                    f"vm={self.vm!r}, "
                    f"name={self.name!r}, "
                    f"user={self.user!r}, "
                    f"user_password={self.user_password!r}, "
                    f"root_password={self.root_password!r})"
                    ")")

        def execute(self):
            from reemote.operations.server.shell import Shell
            from reemote.operations.sftp.write_file import Write_file
            from reemote.operations.sftp.chmod import Chmod
            from reemote.operations.filesystem.chown import Chown
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
            yield Shell("bash /tmp/set_owner.sh", su=True)
            if "alpine" in self.image:
                from reemote.operations.apk.update import Update
                from reemote.operations.apk.upgrade import Upgrade
                from reemote.operations.apk.packages import Packages
                yield Update(sudo=True)
                yield Upgrade(sudo=True)
                yield Packages(packages=["nginx","ufw"],present=True,sudo=True)
            if "debian" in self.image or "ubuntu" in self.image:
                from reemote.operations.apt.update import Update
                from reemote.operations.apt.upgrade import Upgrade
                from reemote.operations.apt.packages import Packages
                yield Update(sudo=True)
                yield Upgrade(sudo=True)
                yield Packages(packages=["nginx","ufw"],present=True,sudo=True)
            if "centos" in self.image:
                from reemote.operations.dnf.update import Update
                from reemote.operations.dnf.upgrade import Upgrade
                from reemote.operations.dnf.packages import Packages
                yield Update(sudo=True)
                yield Upgrade(sudo=True)
                yield Packages(packages=["nginx","ufw"],present=True,sudo=True)
            yield Shell("ufw allow 'Nginx Full'",sudo=True)
            if "centos" in self.image:
                yield Chown(path='/usr/share/nginx/html', owner="kim", group="kim")
                yield Chown(path='/usr/share/nginx/html/index.html', owner="kim", group="kim")
                yield Chmod(
                    path='/usr/share/nginx/html',
                    mode=0o755,
                )
                yield Chmod(
                    path='/usr/share/nginx/html/index.html',
                    mode=0o755,
                )
                yield Write_file(path='/usr/share/nginx/html/index.html',
                                 text=f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title> {args.vm}</title>
</head>
<body>
    <h1>{args.image} virtual machine {args.vm} started</h1>
    <p>ssh access:</p>
    <p>ssh {args.user}@{ip_address}</p>
    <p>using password: {args.user_password}</p>
    <p>The user {args.user} has been added to the sudoers file</p>
    <p>Wrote inventory file inventory-{args.vm}.py</p>
</body>
</html>
""")
            else:
                yield Chown(path='/var/www/html/', owner="kim", group="kim")
                yield Chmod(
                    path='/var/www/html/',
                    mode=0o755,
                )
                yield Write_file(path='/var/www/html/index.html',
                                 text=f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title> {args.vm}</title>
</head>
<body>
    <h1>{args.image} virtual machine {args.vm} started</h1>
    <p>ssh access:</p>
    <p>ssh {args.user}@{ip_address}</p>
    <p>using password: {args.user_password}</p>
    <p>The user {args.user} has been added to the sudoers file</p>
    <p>Wrote inventory file inventory-{args.vm}.py</p>
</body>
</html>
""")
            yield Shell("systemctl restart nginx",sudo=True)

        # Read the inventory file
    inventory_func = read_inventory(inventory_filename)
    responses = await execute(inventory_func(), Setup_sudo_access(
        vm=args.vm,
        image=args.image,
        name=args.name,
        user=args.user,
        user_password=args.user_password,
        root_password=args.root_password,
    ))
    json = produce_json(responses)
    print(json)
    df = convert_to_df(json, columns=["command", "host", "returncode", "stdout", "stderr", "error"])
    table = convert_to_tabulate(df)
    # print(table)



    # Write the output to the console
    output = f"""{args.image} virtual machine {args.vm} started
view credentials at http://{ip_address}
ssh access:
ssh {args.user}@{ip_address}
using password: {args.user_password}
the user {args.user} has been added to the sudoers file
wrote inventory file inventory-{args.vm}.py"""

    print(output)


if __name__ == "__main__":
    asyncio.run(main())
