import asyncio
from commands.sftp import Put, Rmdir, Isdir, Isfile
from construction_tracker import track_construction, track_yields
from execute import execute

@track_construction
class Root:
    @track_yields
    async def execute(self):
        # r =  yield Put(localpaths="/home/kim/restpoc/main13.py", remotepath="/home/user/",group="101")
        # r =  yield Rmdir(path="/home/user/freddy",group="101")
        r =  yield Isdir(path="/home/user/freddy")
        # r =  yield Isfile(path="/home/user/freddy.txt",group="101")

async def main():
    await execute(lambda: Root())

if __name__ == "__main__":
    asyncio.run(main())
