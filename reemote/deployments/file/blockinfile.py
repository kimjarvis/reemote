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
        from reemote.operations.builtin.blockinfile import Blockinfile
        yield Blockinfile(
            path="/etc/example.conf",
            block="Secure block content.",
            marker="# {mark} SECURE BLOCK",
            state="present",
            insertafter="EOF",
            attrs={"permissions": 0o600, "owner": "kim", "group": "kim"}
        )

if __name__ == "__main__":
    asyncio.run(main(Write_text_to_file))
