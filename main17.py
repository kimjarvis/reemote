import asyncio
from commands.sftp import Put, Rmdir, Isdir, Isfile
from construction_tracker import track_construction, track_yields
from execute import execute
from inventory import get_inventory
from utilities.logging import reemote_logging
from response import validate_responses

@track_construction
class Root:
    @track_yields
    async def execute(self):
        # r =  yield Put(localpaths="/home/kim/restpoc/main13.py", remotepath="/home/user/",group="101")
        # r =  yield Rmdir(path="/home/user/freddy",group="101")
        r =  yield Isdir(path="/home/user/freddy")
        # r =  yield Isfile(path="/home/user/freddy.txt",group="101")

async def main():
    reemote_logging()
    responses = await execute(get_inventory(), lambda: Root())
    validated_responses = await validate_responses(responses)

    # Each response is now a UnifiedResult with all fields available:
    for result in validated_responses:
        print(f"Host: {result.host}")
        print(f"Command: {result.command}")
        print(f"Output: {result.output}")
        print(f"Stdout: {result.stdout}")
        print(f"Stderr: {result.stderr}")
        print(f"Executed: {result.executed}")
        print(f"Changed: {result.changed}")

if __name__ == "__main__":
    asyncio.run(main())
