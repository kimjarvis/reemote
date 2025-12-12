import asyncio
from commands.sftp import Put, Rmdir, Isdir, Isfile
from construction_tracker import track_construction, track_yields
from execute import execute
from inventory import get_inventory
from utilities.logging import reemote_logging

@track_construction
class Root:
    @track_yields
    async def execute(self):
        # r =  yield Put(localpaths="/home/kim/restpoc/main13.py", remotepath="/home/user/",group="101")
        # r =  yield Rmdir(path="/home/user/freddy",group="101")
        r =  yield Isdir(path="/home/user/freddy",group="101")
        r =  yield Isfile(path="/home/user/freddy.txt",group="101")
        print(r)

async def main():
    reemote_logging()
    await execute(get_inventory(), lambda: Root())

if __name__ == "__main__":
    asyncio.run(main())
