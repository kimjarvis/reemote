from reemote.operations.apk.packages import Packages
from reemote.operations.apk.update import Update
from reemote.operations.apk.upgrade import Upgrade


class Install_vim:
    def execute(self):
        yield Packages(packages=["asterisk","vim"], present=False, su=True)
        yield Update(su=True)
        yield Packages(packages=["apk add vim=9.1.1566-r0"],repository="http://dl-cdn.alpinelinux.org/alpine/edge/main", present=True, su=True)
        yield Upgrade(su=True)