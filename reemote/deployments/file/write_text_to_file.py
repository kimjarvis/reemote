import asyncio
from reemote.main import main

class Write_text_to_file:
    def execute(self):
        from reemote.operations.sftp.write_file import Write_file
        yield Write_file(path='hello.txt',text='Hello World!')

if __name__ == "__main__":
    asyncio.run(main(Write_text_to_file))
