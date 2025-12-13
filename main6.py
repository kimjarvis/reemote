import asyncio

from execute import execute
from inventory import get_inventory
from utilities.logging import reemote_logging
from utilities.logging import reemote_logging

async def main():
    reemote_logging()
    inventory = get_inventory()
    from commands.apt import Package
    responses = await execute(inventory, lambda: Package(name="apt package tree",
                                                         packages =["tree"],
                                                         present = False,
                                                         group="all",
                                                         sudo=True))
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
