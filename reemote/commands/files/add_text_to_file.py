from reemote.command_install import Command_install
from reemote.command import Command
from reemote.operations.sftp.read_file import Read_file
from reemote.operations.sftp.write_file import Write_file

class Add_text_to_file():
    def __init__(self,
                text="",
                path=""):
        self.text=text
        self.path=path

    def __repr__(self) -> str:
        return (f"Add_text_to_file("
                f"text={self.text!r}, "
                f"path={self.path!r}, "
                )

    def execute(self):
        r=yield Read_file(path=self.path)
        file_text=str(r.cp.stdout)+str(self.text)
        yield Write_file(path=self.path,text=file_text)