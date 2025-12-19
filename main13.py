import asyncio
from reemote.commands.sftp import Put, Get, Copy, Mcopy, Mput, Mget
from reemote.execute import execute


class Root:
    async def execute(self):
        r = yield Put(
            localpaths="/home/kim/restpoc/main13.py", remotepath="/home/user/"
        )
        r = yield Get(remotepaths="/home/user/main13.py", localpath="/tmp")
        r = yield Copy(srcpaths="/home/user/main13.py", dstpath="/home/user/main14.py")
        r = yield Mcopy(srcpaths="/home/user/main*.py", dstpath="/home/user/fred")
        r = yield Mput(
            localpaths="/home/kim/restpoc/main1*.py", remotepath="/home/user/"
        )
        r = yield Mget(remotepaths="/home/user/main1*.py", localpath="/tmp")


async def main():
    await execute(lambda: Root())


if __name__ == "__main__":
    asyncio.run(main())
