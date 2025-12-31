import asyncio

from reemote.api.scp import Copy, Download, Upload
from reemote.execute import endpoint_execute


class Root:
    async def execute(self):
        print("upload")
        r = yield Upload(
            name="upload",
            srcpaths=['/etc/hosts','/etc/passwd'],
            dstpath='/home/user/',
        )
        print("download")
        r = yield Download(
            name="download",
            srcpaths=['/home/user/hosts','/home/user/passwd'],
            dstpath='/tmp/',
            group="A" # This is the source, where execute runs it
        )
        print("copy")
        r = yield Copy(
            name="copy",
            srcpaths=['/etc/hosts','/etc/passwd'],
            dstpath='/home/user/',
            group="A", # This is the source, where execute runs it
            dsthost="192.168.1.24"
        )

        print(r)


async def main():
    await endpoint_execute(lambda: Root())

if __name__ == "__main__":
    asyncio.run(main())
