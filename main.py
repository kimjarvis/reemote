import asyncio
from inventory import get_inventory
from execute import execute
from utilities.validate_responses import validate_responses

class Hello:
    async def execute(self):
        from commands.server import Shell
        r = yield Shell(name="echo",
                     cmd="echo Hello World!",
                     group="All",
                     sudo=False)

class Root:
    async def execute(self):
        yield Hello()


async def main():
    inventory = get_inventory()
    print(f"Inventory: {inventory}")
    responses = await execute(inventory, lambda: Root())
    validated_responses = await validate_responses(responses)
    print(validated_responses)

if __name__ == "__main__":
    asyncio.run(main())