import asyncio
from inventory import get_inventory
from execute import execute
from response import validate_responses
import logging
from utilities.logging import reemote_logging
from utilities.checks import flatten
from construction_tracker import ConstructionTracker
from commands.sftp import Isdir, Isfile, Mkdir, Rmdir, Stat, Get,Put
from construction_tracker import track_construction, track_yields

@track_construction
class Root:
    @track_yields
    async def execute(self):
        # r = yield Put(localpaths="/home/kim/restpoc/main13.py",remotepath="/home/user/")
        r = yield Get(remotepaths="/home/user/main13.py",localpath="/tmp")

async def main():
    logging.basicConfig(
        level=logging.DEBUG,
        filename="debug.log",  # Log file name
        filemode="w",  # Overwrite the file each time
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    inventory = get_inventory()
    responses = await execute(inventory, lambda: Root())
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
