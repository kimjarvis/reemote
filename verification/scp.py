import asyncio

from reemote.commands.scp import Upload, Download, Copy
from reemote.commands.sftp import Isfile
from reemote.execute import execute


class Root:
    async def execute(self):
        print("upload")
        # Implement sftp remove file
        r = yield Upload(
            name="Upload",
            srcpaths=['/etc/hosts','/etc/passwd'],
            dstpath='/home/user/',
        )
        r = yield Isfile(name="IsFile",path="/home/user/hosts",group="A")
        assert r.value == [{'value': True}], "File does not exist"
        print("download")
        r = yield Download(
            name="Download",
            srcpaths=['/home/user/hosts','/home/user/passwd'],
            dstpath='/tmp/',
            group="A"
        )
        r = yield Isfile(path="/tmp/hosts",group="local")
        assert r.value == [{'value': True}], "File does not exist"
        print("copy")
        r = yield Copy(
            name="Copy",
            srcpaths=['/etc/hosts','/etc/passwd'],
            dstpath='/home/user/',
            group="A", # This is the source
            dsthost="192.168.1.24"
        )
        r = yield Isfile(path="/home/user/hosts",group="B")
        assert r.value == [{'value': True}], "File does not exist"

async def main():
    await execute(lambda: Root())

if __name__ == "__main__":
    asyncio.run(main())
