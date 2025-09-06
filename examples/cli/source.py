from reemote.operations.filesystem.directory import Directory

class Make_directory:
    def execute(self):
        yield Directory(path="/tmp/mydir", present=True, sudo=False)
