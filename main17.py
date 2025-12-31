import asyncio
from reemote.api.sftp import Isdir
from reemote.execute import endpoint_execute

class Root:
    async def execute(self):
        # r =  yield Put(localpaths="/home/kim/restpoc/main13.py", remotepath="/home/user/",group="101")
        # r =  yield Rmdir(path="/home/user/freddy",group="101")
        r =  yield Isdir(path="/home/user/freddy")
        # r =  yield Isfile(path="/home/user/freddy.txt",group="101")

async def main():
    await endpoint_execute(lambda: Root())

if __name__ == "__main__":
    asyncio.run(main())
