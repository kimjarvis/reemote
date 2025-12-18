import asyncio
from reemote.execute import execute
from reemote.commands.sftp import Isdir

async def main():
    await execute(lambda: Isdir(path="/home/user/freddo7"))

if __name__ == "__main__":
    asyncio.run(main())
