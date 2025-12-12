import asyncio
from construction_tracker import track_construction, track_yields
from execute import execute
from inventory import get_inventory
from response import validate_responses
from utilities.logging import reemote_logging
from commands.scp import Upload, Download
from pathlib import Path
from commands.sftp import isfile, mkdir

@track_construction
class Root:
    @track_yields
    async def execute(self):
        # r = yield Upload(
        #     srcpaths=['/etc/hosts','/etc/passwd'],
        #     dstpath='/home/user/'
        # )
        r = yield Download(
            srcpaths=['/home/user/hosts','/home/user/passwd'],
            dstpath='/tmp/',
            group="101"
        )
        print(r)


async def main():
    reemote_logging()
    inventory = get_inventory()
    responses = await execute(inventory, lambda: Root())
    # validated_responses = await validate_responses(responses)
    # # Each response is now a UnifiedResult with all fields available:
    # for result in validated_responses:
    #     print(f"Host: {result.host}")
    #     print(f"Command: {result.command}")
    #     print(f"Output: {result.output}")
    #     print(f"Stdout: {result.stdout}")
    #     print(f"Stderr: {result.stderr}")
    #     print(f"Changed: {result.changed}")

if __name__ == "__main__":
    asyncio.run(main())
