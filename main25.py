import asyncio
from reemote.execute import execute
from reemote.commands.sftp import Mkdir

class Root:
    async def execute(self):
        from reemote.commands.scp import Copy

        r = yield Copy(srcpaths=["/home/user/testdata/file_b.txt"], dstpath="/home/user/testdata/file_c.txt",
                       group="192.168.1.24", dsthost="192.168.1.76")
        print(r)

async def main():
    await execute(lambda: Root())

if __name__ == "__main__":
    asyncio.run(main())
