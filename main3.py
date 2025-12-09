import asyncio
from inventory import get_inventory
from execute import execute
from response import validate_responses
from construction_tracker import ConstructionTracker, track_construction, track_yields


async def main():
    inventory = get_inventory()
    from commands.apt import Install
    responses = await execute(inventory, lambda: Install(name="install tree",
                                                         packages =["tree","vim"],
                                                         group="All",
                                                         sudo=True))
    validated_responses = await validate_responses(responses)
    print(validated_responses)

    # Each response is now a UnifiedResult with all fields available:
    for result in validated_responses:
        print(f"Host: {result.host}")
        print(f"Command: {result.command}")
        print(f"Output: {result.output}")
        print(f"Stdout: {result.stdout}")
        print(f"Stderr: {result.stderr}")
        print(f"Changed: {result.changed}")
    # Print the construction hierarchy at the end
    print("\nConstruction Hierarchy:")
    ConstructionTracker.print()

if __name__ == "__main__":
    asyncio.run(main())
