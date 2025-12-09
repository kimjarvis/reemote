import asyncio
from inventory import get_inventory
from execute import execute
from response import validate_responses
from utilities.logging import reemote_logging
from utilities.checks import flatten
from construction_tracker import ConstructionTracker
from commands.sftp import Isdir, Isfile, Mkdir, Rmdir, Stat
from construction_tracker import track_construction, track_yields

@track_construction
class Root:
    @track_yields
    async def execute(self):
        r = yield Mkdir(path="/home/user/fred")
        r = yield Stat(path="/home/user/fred",follow_symlinks=True)

async def main():
    reemote_logging()
    inventory = get_inventory()
    responses = await execute(inventory, lambda: Root())
    validated_responses = await validate_responses(responses)
    # Each response is now a UnifiedResult with all fields available:
    for result in validated_responses:
        print(f"Host: {result.host}")
        print(f"Command: {result.command}")
        print(f"Output: {result.output}")
        # print(f"Error: {result.error}")
        print(f"Stdout: {result.stdout}")
        print(f"Stderr: {result.stderr}")
        print(f"Changed: {result.changed}")

if __name__ == "__main__":
    asyncio.run(main())
