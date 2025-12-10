import asyncio
from inventory import get_inventory
from execute import execute
from response import validate_responses
from construction_tracker import  track_construction, track_yields
import logging
async def main():
    logging.basicConfig(
        level=logging.DEBUG,
        filename="debug.log",  # Log file name
        filemode="w",  # Overwrite the file each time
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    # Create a named logger "reemote"
    logger = logging.getLogger("reemote")
    logger.setLevel(logging.DEBUG)  # Set desired log level for your logger

    # Suppress asyncssh logs by setting its log level to WARNING or higher
    logging.getLogger("asyncssh").setLevel(logging.WARNING)

    logging.debug("we are logging!")
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
