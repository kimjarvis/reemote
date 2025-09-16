

class Hello_world:
    def execute(self):
        from reemote.operations.server.shell import Shell
        r0 = yield Shell("echo 'Hello'")
        print(r0.cp.stdout)
        r1 = yield Shell("echo 'World!'")
        print(r1.cp.stdout)

class Install_wget:
    def execute(self):
        from reemote.operations.apt.packages import Packages
        from reemote.operations.server.shell import Shell
        r0 = yield Packages(packages=["wget"], present=True, sudo=True)
        r1 = yield Shell("which wget")
        print(r1.cp.stdout)

class Get_OS:
    def execute(self):
        from reemote.operations.server.shell import Shell
        import re
        r0 = yield Shell("cat /etc/os-release")
        # Extract OS name and version
        os_name_match = re.search(r'PRETTY_NAME="([^"]+)"', r0.cp.stdout)
        os_version_match = re.search(r'VERSION="([^"]+)"', r0.cp.stdout)

        if os_name_match and os_version_match:
            os_name = os_name_match.group(1).split()[0]  # Extract "Debian" from "Debian GNU/Linux"
            os_version = os_version_match.group(1)       # Extract "13 (trixie)"
            print(f"OS Name: {os_name} {os_version}")
        else:
            print("Failed to extract OS details.")

class Get_OS:
    def execute(self):
        from reemote.operations.server.shell import Shell
        import re
        r0 = yield Shell("cat /etc/os-release")
        # Extract OS name and version
        os_name_match = re.search(r'PRETTY_NAME="([^"]+)"', r0.cp.stdout)
        os_version_match = re.search(r'VERSION="([^"]+)"', r0.cp.stdout)

        if os_name_match and os_version_match:
            os_name = os_name_match.group(1).split()[0]  # Extract "Debian" from "Debian GNU/Linux"
            os_version = os_version_match.group(1)       # Extract "13 (trixie)"
            r0.cp.stdout = f"{os_name} {os_version}"
        else:
            r0.cp.stdout = "Failed to extract OS details."


class Show_OS:
    def execute(self):
        r0 = yield Get_OS()
        print(r0.cp.stdout)
