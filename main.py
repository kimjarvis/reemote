import asyncio
from inventory import get_inventory
from execute import execute


class Hello:
    async def execute(self):
        from commands.server import Shell
        r = yield Shell(name="echo",
                     cmd="echo Hello World!",
                     group="All",
                     sudo=False)
        print(f"Result: {r}")
        # from commands.apt import Install
        # r = yield Install(name="install",
        #              packages=["vim"],
        #              sudo=True)
        # print(f"Result: {r}")


class Root:
    async def execute(self):
        result = yield Hello()
        print(f"Root got result: {result}")


async def main():
    inventory = get_inventory()
    print(f"Inventory: {inventory}")
    responses = await execute(inventory, lambda: Root())
    print(f"Responses: {responses}")

if __name__ == "__main__":
    asyncio.run(main())