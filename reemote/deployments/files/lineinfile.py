import asyncio
from reemote.main import main
import re
import asyncssh
from asyncssh.sftp import SFTPAttrs

class Write_text_to_file:
    def execute(self):
        # from reemote.builtins.lineinfile import Lineinfile
        from reemote.operations.files.lineinfile import Lineinfile
        yield Lineinfile(
            line='new_config_value',
            path='/etc/config.conf',
            insertafter='^# Server configuration',  # Note: "Sever" not "Server"
            attrs = {'permissions': 0o755}  # Directly pass a dictionary
        )

if __name__ == "__main__":
    asyncio.run(main(Write_text_to_file))
