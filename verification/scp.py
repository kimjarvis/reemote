import asyncio

from commands.scp import Upload, Download, Copy
from commands.sftp import Isfile
from construction_tracker import track_construction, track_yields
from execute import execute
from inventory import get_inventory
from utilities.logging import reemote_logging


@track_construction
class Root:
    @track_yields
    async def execute(self):
        print("upload")
        # Implement sftp remove file
        r = yield Upload(
            name="Upload",
            srcpaths=['/etc/hosts','/etc/passwd'],
            dstpath='/home/user/',
        )
        r = yield Isfile(name="IsFile",path="/home/user/hosts",group="A")
        if r.executed:
            assert r.output == [{'value': True}], "File does not exist"
        print("download")
        r = yield Download(
            name="Download",
            srcpaths=['/home/user/hosts','/home/user/passwd'],
            dstpath='/tmp/',
            group="A"
        )
        r = yield Isfile(path="/tmp/hosts",group="local")
        if r.executed:
            assert r.output == [{'value': True}], "File does not exist"
        print("copy")
        r = yield Copy(
            name="Copy",
            srcpaths=['/etc/hosts','/etc/passwd'],
            dstpath='/home/user/',
            group="A", # This is the source
            dstgroup="B"
        )
        r = yield Isfile(path="/home/user/hosts",group="B")
        if r.executed:
            assert r.output == [{'value': True}], "File does not exist"

async def main():
    reemote_logging()
    inventory = get_inventory()
    responses = await execute(inventory, lambda: Root())
    # validated_responses = await validate_responses(responses)
    # Each response is now a UnifiedResult with all fields available:
    # for result in validated_responses:
    #     print(f"Host: {result.host}")
    #     print(f"Name: {result.name}")
    #     print(f"Command: {result.command}")
    #     print(f"Output: {result.output}")
    #     print(f"Stdout: {result.stdout}")
    #     print(f"Stderr: {result.stderr}")
    #     print(f"Executed: {result.executed}")
    #     print(f"Changed: {result.changed}")

if __name__ == "__main__":
    asyncio.run(main())
