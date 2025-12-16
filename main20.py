import asyncio
from execute import execute
from commands.sftp import Isdir

async def main():
    await execute(lambda: Isdir(path="/home/user"))

if __name__ == "__main__":
    asyncio.run(main())
