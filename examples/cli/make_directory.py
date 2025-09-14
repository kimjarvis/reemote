from reemote.operations.filesystem.mkdir import Mkdir

class Make_directory:
    def execute(self):
        yield Mkdir(path="/tmp/mydir", present=True, su=True, guard=True)
