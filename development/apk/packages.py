from reemote.operations.apk.packages import Packages

class Install_vim:
    def execute(self):
        yield Packages(packages=["asterisk", "vim"], present=False, su=True)

