from reemote.operations.filesystem.chown import Chown

class Own_directory:
    def execute(self):
        yield Chown(target="/tmp/mydir", user="root", group="root",sudo=True)
