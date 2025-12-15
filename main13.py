import asyncio

from commands.sftp import Mput
from construction_tracker import track_construction, track_yields
from execute import execute
from inventory import get_inventory
from response import validate_responses
from utilities.logging import reemote_logging


@track_construction
class Root:
    @track_yields
    async def execute(self):
        # r = yield Put(localpaths="/home/kim/restpoc/main13.py",remotepath="/home/user/")
        # r = yield Get(remotepaths="/home/user/main13.py",localpath="/tmp")
        # r = yield Copy(srcpaths="/home/user/main13.py",dstpath="/home/user/main14.py")
        # r = yield Mcopy(srcpaths="/home/user/main*.py",dstpath="/home/user/fred")
        r = yield Mput(localpaths="/home/kim/restpoc/main1*.py",remotepath="/home/user/")
        # r = yield Mget(remotepaths="/home/user/main1*.py",localpath="/tmp")

async def main():
    responses = await execute(lambda: Root())
    validated_responses = await validate_responses(responses)
    # Each response is now a UnifiedResult with all fields available:
    for result in validated_responses:
        print(f"Host: {result.host}")
        print(f"Command: {result.command}")
        print(f"Output: {result.output}")
        print(f"Stdout: {result.stdout}")
        print(f"Stderr: {result.stderr}")
        print(f"Changed: {result.changed}")

if __name__ == "__main__":
    asyncio.run(main())
