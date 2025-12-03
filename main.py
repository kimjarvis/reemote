import asyncio
import json
from inventory import get_inventory

from commands.server import shell

class Hello_world:
    def execute(self):
        r = yield shell(cmd="echo Hello World!")

async def main():
    responses = await execute(get_inventory(), Hello_world())
    print(json.dumps(responses))

if __name__ == "__main__":
    asyncio.run(main())