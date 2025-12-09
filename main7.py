import asyncio
import asyncssh
from inventory import get_inventory
from execute import execute
from response import validate_responses
import logging
from construction_tracker import ConstructionTracker


async def main():
    logging.basicConfig(
        level=logging.DEBUG,
        filename="asyncssh_debug.log",  # Log file name
        filemode="w",  # Overwrite the file each time
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    logging.debug("main7")
    inventory = get_inventory()
    from commands.sftp import Mkdir
    responses = await execute(inventory, lambda: Mkdir(name="Make directory fred",
                                                       path="/home/user/h2",
                                                       permissions=0o555))
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

    # Print the construction hierarchy at the end
    print("\nConstruction Hierarchy:")
    ConstructionTracker.print()


if __name__ == "__main__":
    asyncio.run(main())
