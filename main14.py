import asyncio

from reemote.commands.scp import Copy, Download, Upload
from construction_tracker import track_construction, track_yields
from reemote.execute import execute


@track_construction
class Root:
    @track_yields
    async def execute(self):
        print("upload")
        r = yield Upload(
            name="upload",
            srcpaths=['/etc/hosts','/etc/passwd'],
            dstpath='/home/user/',
            # group="C"
        )
        print("download")
        r = yield Download(
            name="download",
            srcpaths=['/home/user/hosts','/home/user/passwd'],
            dstpath='/tmp/',
            group="A"
        )
        print("copy")
        r = yield Copy(
            name="copy",
            srcpaths=['/etc/hosts','/etc/passwd'],
            dstpath='/home/user/',
            group="A", # This is the source
            dstgroup="A"
        )

        print(r)


async def main():
    await execute(lambda: Root())

if __name__ == "__main__":
    asyncio.run(main())
