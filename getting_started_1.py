import asyncio
from reemote.execute import execute
from reemote.commands.server import Shell
from reemote.inventory import create_inventory


async def main():
    response = await execute(lambda: Shell(cmd="echo Hello World!"))
    for r in response:
        print(r.stdout)

if __name__ == "__main__":
    asyncio.run(main())