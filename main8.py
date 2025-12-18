# main.py
import asyncio
from reemote.inventory import get_inventory
from reemote.execute import execute
from construction_tracker import  track_construction, track_yields
from reemote.commands.server import Shell

@track_construction
class Hello:
    @track_yields
    async def execute(self):
        a = yield Shell(cmd="pwd")
        print(type(a))
        b = yield Shell(name="echo",
                        cmd="echo Hello World!",
                        group="all",
                        sudo=False)
        c = yield Shell(cmd="ls -ltr")

@track_construction
class Parent:
    @track_yields
    async def execute(self):
        a = yield Shell(cmd="hostname")
        r = yield Hello()

@track_construction
class Root:
    @track_yields
    async def execute(self):
        r = yield Parent()
        for x in r:
            print(x)

async def main():
    # Clear construction tracker at the beginning
    ConstructionTracker.clear()

    responses = await execute(lambda: Root())
    # validated_responses = await validate_responses(responses)
    # print(validated_responses)
    #
    # # Each response is now a UnifiedResult with all fields available:
    # for result in validated_responses:
    #     print(f"Host: {result.host}")
    #     print(f"Command: {result.command}")
    #     print(f"Output: {result.output}")
    #     print(f"Error: {result.error}")
    #     print(f"Stdout: {result.stdout}")
    #     print(f"Stderr: {result.stderr}")
    #     print(f"Changed: {result.changed}")
    #     print(f"Executed: {result.executed}")
    #
    # # Print the construction hierarchy at the end
    # print("\nConstruction Hierarchy:")
    # ConstructionTracker.print()


if __name__ == "__main__":
    asyncio.run(main())