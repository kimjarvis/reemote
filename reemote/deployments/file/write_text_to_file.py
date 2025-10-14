# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import asyncio
from reemote.main import main

class Write_text_to_file:
    def execute(self):
        from reemote.operations.sftp.write_file import Write_file
        yield Write_file(path='hello.txt',text='Hello World!')

if __name__ == "__main__":
    asyncio.run(main(Write_text_to_file))
