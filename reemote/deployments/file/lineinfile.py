# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import asyncio
from reemote.main import main
import re
import asyncssh
from asyncssh.sftp import SFTPAttrs

class Write_text_to_file:
    def execute(self):
        from reemote.operations.builtin.lineinfile import Lineinfile
        yield Lineinfile(
            line='new_config_value',
            path='/etc/config.conf',
            insertafter='^# Server configuration',  # Note: "Sever" not "Server"
            attrs = {'permissions': 0o755}  # Directly pass a dictionary
        )

if __name__ == "__main__":
    asyncio.run(main(Write_text_to_file))
