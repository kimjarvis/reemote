import asyncio
from reemote.main import main


class Install:
    def execute(self):
        import time
        from reemote.operations.server.shell import Shell
        yield Shell("/snap/bin/lxc --debug storage create disks zfs size=100GiB",
                    sudo=True)
        time.sleep(1)  # Sleeps for 1 second
        yield Shell("/snap/bin/lxc --debug storage set disks volume.size 10GiB",
                    sudo=True)
        time.sleep(1)  # Sleeps for 1 second
        yield Shell("/snap/bin/lxc --debug storage volume create disks local1 --type block",
                    sudo=True)
        time.sleep(1)  # Sleeps for 1 second
        yield Shell("/snap/bin/lxc --debug storage volume create disks local2 --type block",
                    sudo=True)
        time.sleep(1)  # Sleeps for 1 second
        yield Shell("/snap/bin/lxc storage volume create disks local3 --type block",
                    sudo=True)
        time.sleep(1)  # Sleeps for 1 second
        yield Shell("/snap/bin/lxc storage volume create disks local4 --type block",
                    sudo=True)
        time.sleep(1)  # Sleeps for 1 second
        yield Shell("/snap/bin/lxc storage volume create disks remote1 --type block size=20GiB",
                    sudo=True)
        time.sleep(1)  # Sleeps for 1 second
        yield Shell("/snap/bin/lxc storage volume create disks remote2 --type block size=20GiB",
                    sudo=True)
        time.sleep(1)  # Sleeps for 1 second
        yield Shell("/snap/bin/lxc storage volume create disks remote3 --type block size=20GiB",
                    sudo=True)



        from reemote.facts.server.get_os import Get_OS

        # # Yield the Get_OS operation to the framework
        # osx = yield Get_OS("NAME")
        # print(osx.cp.stdout)
        # if 'Debian' in osx.cp.stdout:
        #     print("Debian detected")
        #     from reemote.operations.apt.update import Update
        #     yield Update(sudo=True)
        #     from reemote.operations.apt.packages import Packages
        #     yield Packages(packages=["snapd","expect"],present=True,sudo=True)
        #
        #     from reemote.operations.snap.packages import Packages
        #     yield Packages(packages=["snapd"],present=True,sudo=True)
        #     yield Packages(packages=["microcloud"],present=True,sudo=True)
        # else:
        #     print("OS Not supported")
        #
        # # This should be snap install !
        # from reemote.operations.snap.packages import Get_packages
        # snaps = yield Get_packages()
        # print(type(snaps.cp.stdout))
        # if not any(item['name'] == 'lxc' for item in snaps.cp.stdout):
        #     from reemote.operations.snap.packages import Install
        #     yield Install(packages=["lxd"],channel="5.21/stable",sudo=True)
        # else:
        #     from reemote.operations.snap.packages import Refresh
        #     yield Refresh(packages=["lxd"],channel="5.21/stable",sudo=True)

        # add software sources contrib and non-free to /etc/apt/sources.list
        # apt update
        # install zfsutils-linux
        # Install headers sudo apt install linux-headers-$(uname -r)
        # Follow instructions here https://wiki.debian.org/ZFS#Installation to add backports
        # The apt install builds the zfs kernel module
        # sudo modprobe zfs
        # Configure LXD to use external tools sudo snap set lxd zfs.external=true
        # Restart the snap service sudo snap restart lxd



#
#
#         from reemote.operations.server.shell import Shell
#         yield Shell('export PATH = "$PATH:/snap/bin"')
#
#         from reemote.operations.server.executable import Executable
#         yield Executable(text="""#!/usr/bin/expect
# set timeout 30
# spawn sudo /snap/bin/lxd init
#
# sleep 2
#
# expect {
#     timeout {
#         send_user "\nTimeout waiting for clustering prompt\n"
#         exit 1
#     }
#     "clustering?" {
#         send "no\r"
#     }
# }
#
# sleep 1
# expect {
#     timeout {
#         send_user "\nTimeout waiting for storage pool prompt\n"
#         exit 1
#     }
#     "storage pool?" {
#         send "yes\r"
#     }
# }
#
# sleep 1
# expect {
#     timeout {
#         send_user "\nTimeout waiting for storage pool name prompt\n"
#         exit 1
#     }
#     "Name of the new storage pool" {
#         send "default\r"
#     }
# }
#
# sleep 1
# expect {
#     timeout {
#         send_user "\nTimeout waiting for storage backend prompt\n"
#         exit 1
#     }
#     "storage backend" {
#         send "btrfs\r"
#     }
# }
#
# sleep 1
# expect {
#     timeout {
#         send_user "\nTimeout waiting for BTRFS pool prompt\n"
#         exit 1
#     }
#     "Create a new BTRFS pool?" {
#         send "yes\r"
#     }
# }
#
# sleep 1
# expect {
#     timeout {
#         send_user "\nTimeout waiting for block device prompt\n"
#         exit 1
#     }
#     "existing empty block device" {
#         send "no\r"
#     }
# }
#
# sleep 1
# expect {
#     timeout {
#         send_user "\nTimeout waiting for loop device size prompt\n"
#         exit 1
#     }
#     "Size in GiB" {
#         send "40\r"
#     }
# }
#
# sleep 1
# expect {
#     timeout {
#         send_user "\nTimeout waiting for MAAS server prompt\n"
#         exit 1
#     }
#     "MAAS server" {
#         send "no\r"
#     }
# }
#
# sleep 1
# expect {
#     timeout {
#         send_user "\nTimeout waiting for network bridge prompt\n"
#         exit 1
#     }
#     "local network bridge" {
#         send "yes\r"
#     }
# }
#
# sleep 1
# expect {
#     timeout {
#         send_user "\nTimeout waiting for bridge name prompt\n"
#         exit 1
#     }
#     "new bridge be called" {
#         send "lxdbr0\r"
#     }
# }
#
# sleep 1
# expect {
#     timeout {
#         send_user "\nTimeout waiting for IPv4 address prompt\n"
#         exit 1
#     }
#     "IPv4 address" {
#         send "10.1.123.1/24\r"
#     }
# }
#
# sleep 1
# expect {
#     timeout {
#         send_user "\nTimeout waiting for IPv4 NAT prompt\n"
#         exit 1
#     }
#     "NAT IPv4 traffic" {
#         send "yes\r"
#     }
# }
#
# sleep 1
# expect {
#     timeout {
#         send_user "\nTimeout waiting for IPv6 address prompt\n"
#         exit 1
#     }
#     "IPv6 address" {
#         send "fd42:1:1234:1234::1/64\r"
#     }
# }
#
# sleep 1
# expect {
#     timeout {
#         send_user "\nTimeout waiting for IPv6 NAT prompt\n"
#         exit 1
#     }
#     "NAT IPv6 traffic" {
#         send "yes\r"
#     }
# }
#
# sleep 1
# expect {
#     timeout {
#         send_user "\nTimeout waiting for network availability prompt\n"
#         exit 1
#     }
#     "available over the network" {
#         send "no\r"
#     }
# }
#
# sleep 1
# expect {
#     timeout {
#         send_user "\nTimeout waiting for bind address prompt\n"
#         exit 1
#     }
#     "Address to bind LXD to" {
#         send "all\r"
#     }
# }
#
# sleep 1
# expect {
#     timeout {
#         send_user "\nTimeout waiting for port prompt\n"
#         exit 1
#     }
#     "Port to bind LXD to" {
#         send "8443\r"
#     }
# }
#
# sleep 1
# expect {
#     timeout {
#         send_user "\nTimeout waiting for stale images prompt\n"
#         exit 1
#     }
#     "stale cached images" {
#         send "yes\r"
#     }
# }
#
# sleep 1
# expect {
#     timeout {
#         send_user "\nTimeout waiting for YAML preseed prompt\n"
#         exit 1
#     }
#     "YAML.*preseed" {
#         send "no\r"
#     }
# }
#
# expect eof""",sudo=True)
#
#         from reemote.operations.server.shell import Shell
#         yield Shell('export PATH = "$PATH:/snap/bin"')
#
#         from reemote.operations.server.shell import Shell
#         yield Shell("/snap/bin/lxc network set ens18 ipv6.dhcp.stateful true",sudo=True)

if __name__ == "__main__":
    asyncio.run(main(Install))