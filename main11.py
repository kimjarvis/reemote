import asyncio
from inventory import get_inventory
from execute import execute
from response import validate_responses
import logging
from construction_tracker import ConstructionTracker
from commands.sftp import Isdir, Isfile



async def main():
    logging.basicConfig(
        level=logging.DEBUG,
        filename="asyncssh_debug.log",  # Log file name
        filemode="w",  # Overwrite the file each time
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    inventory = get_inventory()

    responses = await execute(inventory, lambda: Isdir(path="/home/user/fred"))
    validated_responses = await validate_responses(responses)

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

if __name__ == "__main__":
    asyncio.run(main())
