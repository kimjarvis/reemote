import asyncio
from reemote.execute import endpoint_execute

class Root:
    async def execute(self):
        from reemote.api.scp import Copy

        r = yield Copy(srcpaths=["/home/user/testdata/file_b.txt"], dstpath="/home/user/testdata/file_c.txt",
                       group="192.168.1.24", dsthost="192.168.1.76")
        print(r)

async def main():
    await endpoint_execute(lambda: Root())

if __name__ == "__main__":
    asyncio.run(main())
