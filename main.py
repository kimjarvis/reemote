import asyncio
import json
from inventory import get_inventory
from execute import execute


class Hello:
    async def execute(self):
        from commands.server import Shell
        r = yield Shell(name="echo",
                     cmd="echo Hello World!",
                     sudo=False)
        print(f"Result: {r}")


class Root:
    async def execute(self):
        result = yield Hello()
        print(f"Root got result: {result}")


async def main():
    inventory = get_inventory()
    print(f"Inventory: {inventory}")
    responses = await execute(inventory, Root())
    print(f"Responses: {responses}")

if __name__ == "__main__":
    asyncio.run(main())