import asyncio
import json
from inventory import get_inventory
from execute import execute


class Hello:
    async def execute(self):
        from commands.server import Shell
        shell = Shell(name="echo",
                     cmd="echo Hello World!",
                     sudo=False)
        r = yield shell  # Yield the Shell object
        print(f"Result: {r}")


class Root:
    async def execute(self):
        hello = Hello()

        # Simply yield the Hello instance
        # The framework will call hello.execute() itself
        result = yield hello
        print(f"Root got result: {result}")


async def main():
    inventory = get_inventory()
    print(f"Inventory: {inventory}")
    responses = await execute(inventory, Root())
    print(f"Responses: {responses}")

if __name__ == "__main__":
    asyncio.run(main())