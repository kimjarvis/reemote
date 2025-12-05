import asyncio
from inventory import get_inventory
from execute import execute
import logging
from utilities.validate_responses import validate_responses


async def main():
    logging.basicConfig(
        level=logging.DEBUG,
        filename="asyncssh_debug.log",  # Log file name
        filemode="w",  # Overwrite the file each time
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    inventory = get_inventory()
    print(f"Inventory: {inventory}")
    from commands.apt import Install
    responses = await execute(inventory, lambda: Install(name="install tree",
                     packages =["tree","vim"],
                     group="All",
                     sudo=True))
    print(f"Result: {responses}")
    validated_responses = await validate_responses(responses)
    print(validated_responses)

if __name__ == "__main__":
    asyncio.run(main())