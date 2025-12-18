import asyncio
from reemote.execute import execute
from reemote.commands.server import Shell

async def main():
    await execute(lambda: Shell(cmd="echo Hello World!"))

if __name__ == "__main__":
    asyncio.run(main())
