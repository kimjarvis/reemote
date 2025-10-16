# Copyright (c) 2025 Kim Jarvis TPF Software Services S.A. kim.jarvis@tpfsystems.com 
# This software is licensed under the MIT License. See the LICENSE file for details.
#
import asyncio
from reemote.execute import execute
from reemote.utilities.produce_json import produce_json
from reemote.utilities.produce_table import produce_table
from reemote.operations.server.shell import Shell

from typing import List, Tuple, Dict, Any



class Hello_world:
    def execute(self):
        r = yield Shell("echo Hello World!")
        print(r.cp.stdout)

async def main():
    responses = await execute(inventory(), Hello_world())
    print(produce_table(produce_json(responses)))


if __name__ == "__main__":
    asyncio.run(main())
