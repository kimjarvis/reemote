import asyncio
from execute import execute
from commands.server import Shell

async def main():
    await execute(lambda: Shell(name="test", sudo = True, cmd="ls /root"))

if __name__ == "__main__":
    asyncio.run(main())
