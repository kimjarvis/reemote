# main.py
import asyncio
from inventory import get_inventory
from execute import execute
from response import validate_responses
from construction_tracker import ConstructionTracker, track_construction, track_yields


@track_construction
class Hello:
    @track_yields
    async def execute(self):
        from commands.server import Shell

        yield Shell(cmd="pwd")
        print("trace 00")
        yield Shell(name="echo",
                        cmd="echo Hello World!",
                        group="All",
                        sudo=False)
        print("trace 01")


@track_construction
class Root:
    @track_yields
    async def execute(self):
        yield Hello()


async def main():
    # Clear construction tracker at the beginning
    ConstructionTracker.clear()

    inventory = get_inventory()
    print(f"Inventory: {inventory}")
    responses = await execute(inventory, lambda: Root())
    validated_responses = await validate_responses(responses)
    print(validated_responses)

    # Each response is now a UnifiedResult with all fields available:
    for result in validated_responses:
        print(f"Host: {result.host}")
        print(f"Command: {result.command}")
        print(f"Output: {result.output}")
        print(f"Error: {result.error}")
        print(f"Stdout: {result.stdout}")
        print(f"Stderr: {result.stderr}")
        print(f"Changed: {result.changed}")
        print(f"Executed: {result.executed}")

    # Print the construction hierarchy at the end
    print("\nConstruction Hierarchy:")
    ConstructionTracker.print()


if __name__ == "__main__":
    asyncio.run(main())