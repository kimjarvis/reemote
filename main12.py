import asyncio

from reemote.commands.sftp import Mkdir, Stat
from reemote.construction_tracker import track_construction, track_yields
from reemote.execute import execute


@track_construction
class Root:
    @track_yields
    async def execute(self):
        yield Mkdir(path="/home/user/fred93",group="A")
        yield Stat(path="/home/user/fred93",group="A",follow_symlinks=True)

async def main():
    await execute(lambda: Root())

if __name__ == "__main__":
    asyncio.run(main())
