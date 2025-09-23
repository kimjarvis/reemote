class Shell_example:
    def execute(self):
        from reemote.operations.server.shell import Shell
        # Execute a shell command on all hosts
        r = yield Shell("echo Hello World")
        # The result is available in stdout
        print(r.cp.stdout)

class Put_file_example:
    def execute(self):
        from reemote.operations.filesystem.put_file import Put_file
        from reemote.operations.server.shell import Shell
        # Create a file from text on all hosts
        r = yield Put_file(path='example.txt', text='Hello World!')
        # Verify the file content on the hosts
        r = yield Shell("cat example.txt")
        print(r.cp.stdout)

class Get_file_example:
    def execute(self):
        from reemote.operations.filesystem.get_file import Get_file
        # Get file content from a host
        r = yield Get_file(path='example.txt', host='192.168.122.5')
        # The content is available in stdout
        print(r.cp.stdout)

class Touch_example:
    def execute(self):
        from reemote.operations.filesystem.touch import Touch
        from reemote.operations.server.shell import Shell
        # Create file on all hosts
        r = yield Touch(path='log.txt', present=True)
        # View the file
        r = yield Shell("ls -ltr log.txt")
        print(r.cp.stdout)
        # Remove file from all hosts
        r = yield Touch(path='log.txt', present=False)
        # Check the file
        r = yield Shell("ls -ltr log.txt")
        print(r.cp.stdout)

class Mkdir_example:
    def execute(self):
        from reemote.operations.filesystem.mkdir1 import Mkdir
        from reemote.operations.server.shell import Shell
        # Create directory on all hosts
        r = yield Mkdir(path='mydir', present=True)
        # Remove directory from all hosts
        r = yield Mkdir(path='mydir', present=False)
        # View the directory
        r = yield Shell("ls -ltr .")
        print(r.cp.stdout)

class Chown_example:
    def execute(self):
        from reemote.operations.filesystem.chown import Chown
        from reemote.operations.filesystem.mkdir1 import Mkdir
        from reemote.operations.server.shell import Shell
        # Create directory on all hosts
        r = yield Mkdir(path='mydir', present=True)
        # Change the ownership
        r = yield Chown(path='mydir', user="root", group="root", su=True)
        # View the ownership
        r = yield Shell("ls -ltr .")
        print(r.cp.stdout)

class Chmod_example:
    def execute(self):
        from reemote.operations.filesystem.chmod import Chmod
        from reemote.operations.filesystem.touch import Touch
        from reemote.operations.server.shell import Shell
        # Create file on all hosts
        r = yield Touch(path='script.sh', present=True)
        # Change the permissions
        r = yield Chmod(path='script.sh', options="+x")
        # View the permissions
        r = yield Shell("ls -ltr script.sh")
        print(r.cp.stdout)

class Info_example:
    def execute(self):
        from reemote.operations.apk.info import Info
        # Get the package information on all hosts
        r = yield Info(package='vim')
        # View the package information
        print(r.cp.stdout)

class Update_example:
    def execute(self):
        from reemote.operations.apk.update import Update
        # Update the packages on all hosts
        r = yield Update()

class Upgrade_example:
    def execute(self):
        from reemote.operations.apk.upgrade import Upgrade
        # Upgrade the packages on all hosts
        r = yield Upgrade()


class Packages_example:
    def execute(self):
        from reemote.operations.apk.packages import Packages
        from reemote.operations.server.shell import Shell
        # Add the packages on all hosts
        r = yield Packages(packages=["vim","asterisk"],present=True, su=True)
        # Verify installation
        r = yield Shell("which vim")
        print(r.cp.stdout)
        # Delete the packages on all hosts
        r = yield Packages(packages=["vim","asterisk"],present=False, su=True)
        # Verify removal
        r = yield Shell("which vim")
        print(r.cp.stdout)

class Apt_packages_example:
    def execute(self):
        from reemote.operations.apt.packages import Packages
        from reemote.operations.server.shell import Shell
        # Add the packages on all hosts
        r = yield Packages(packages=["vim"],present=True, sudo=True)
        # Verify installation
        r = yield Shell("which vim")
        print(r.cp.stdout)
        # Delete the packages on all hosts
        r = yield Packages(packages=["vim"],present=False, sudo=True)
        # Verify removal
        r = yield Shell("which vim")
        print(r.cp.stdout)

class Pip_packages_example:
    def execute(self):
        from reemote.operations.pip.packages import Packages
        from reemote.operations.server.shell import Shell
        # Add the packages on all hosts
        r = yield Packages(packages=["requests"],present=True, sudo=True)
        # Verify installation
        r = yield Shell("python3 -c 'import requests; print(requests.__version__)'")
        print(r.cp.stdout)
        # Delete the packages on all hosts
        r = yield Packages(packages=["requests"],present=False, sudo=True)
        # Verify removal
        r = yield Shell("python3 -c 'import requests; print(requests.__version__)'")
        print(r.cp.stdout)

class Pipx_packages_example:
    def execute(self):
        from reemote.operations.pipx.packages import Packages
        from reemote.operations.server.shell import Shell
        # Add the packages on all hosts
        r = yield Packages(packages=["pycowsay"],present=True, su=True)
        # Verify installation
        r = yield Shell("pycowsay moo")
        print(r.cp.stdout)
        # Delete the packages on all hosts
        r = yield Packages(packages=["pycowsay"],present=False, su=True)
        # Verify removal
        r = yield Shell("pycowsay moo")
        print(r.cp.stdout)

class Pacman_packages_example:
    def execute(self):
        from reemote.operations.pacman.packages import Packages
        from reemote.operations.server.shell import Shell
        # Add the packages on all hosts
        r = yield Packages(packages=["vim"],present=True, sudo=True)
        # Verify installation
        r = yield Shell("which vim")
        print(r.cp.stdout)
        # Delete the packages on all hosts
        r = yield Packages(packages=["vim"],present=False, sudo=True)
        # Verify removal
        r = yield Shell("which vim")
        print(r.cp.stdout)

class Pacman_update:
    def execute(self):
        from reemote.operations.pacman.update import Update
        # Update the packages on all hosts
        r = yield Update(sudo=True)

class Get_os_example:
    def execute(self):
        from reemote.facts.server.get_os import Get_OS
        r = yield Get_OS("NAME")


class Get_packages:
    def execute(self):
        from reemote.facts.apt.get_packages import Get_packages
        r = yield Get_packages()
