import asyncio
from inventory import get_inventory
from execute import execute


class Root:
    async def execute(self):
        from commands.server import Shell
        r = yield Shell(name="echo",
                     cmd="echo Hello World!",
                     group="All",
                     sudo=False)
        print(f"Result: {r}")


async def main():
    inventory = get_inventory()
    print(f"Inventory: {inventory}")
    responses = await execute(inventory, lambda: Root())
    print(f"Responses: {responses}")

if __name__ == "__main__":
    asyncio.run(main())