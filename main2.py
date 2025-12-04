import asyncio
from inventory import get_inventory
from execute import execute
import logging

async def main():
    logging.basicConfig(
        level=logging.DEBUG,
        filename="asyncssh_debug.log",  # Log file name
        filemode="w",  # Overwrite the file each time
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    inventory = get_inventory()
    print(f"Inventory: {inventory}")
    from commands.server import Shell
    responses = await execute(inventory, Shell(name="echo",
                     cmd="echo Hello World!",
                     group="All",
                     sudo=False))
    print(f"Responses: {responses}")

if __name__ == "__main__":
    asyncio.run(main())