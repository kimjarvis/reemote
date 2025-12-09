import asyncio
from inventory import get_inventory
from execute import execute
from response import validate_responses

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

    # Each response is now a UnifiedResult with all fields available:
    for result in validated_responses:
        print(f"Host: {result.host}")
        print(f"Command: {result.command}")
        print(f"Output: {result.output}")
        print(f"Error: {result.error}")
        print(f"Stdout: {result.stdout}")
        print(f"Stderr: {result.stderr}")
        print(f"Changed: {result.changed}")

if __name__ == "__main__":
    asyncio.run(main())
