import asyncio

from reemote.commands.sftp import Mkdir, Stat
from reemote.execute import execute



class Root:
    async def execute(self):
        yield Mkdir(path="/home/user/fred93",group="A")
        yield Stat(path="/home/user/fred93",group="A",follow_symlinks=True)

async def main():
    await execute(lambda: Root())

if __name__ == "__main__":
    asyncio.run(main())
