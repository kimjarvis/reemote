from reemote.operations.filesystem.directory import Directory
from reemote.operations.filesystem.chown import Chown

class Create_own_directory:
    def execute(self):
        yield Directory(path="/tmp/mydir", present=True, sudo=True)
        yield Chown(target="/tmp/mydir", user="root", group="root",sudo=True)

