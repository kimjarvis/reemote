import asyncio

from reemote.commands.sftp import Mkdir
from reemote.facts.sftp import Stat
from reemote.execute import endpoint_execute



class Root:
    async def execute(self):
        yield Mkdir(path="/home/user/fred93",group="A")
        yield Stat(path="/home/user/fred93",group="A",follow_symlinks=True)

async def main():
    await endpoint_execute(lambda: Root())

if __name__ == "__main__":
    asyncio.run(main())
