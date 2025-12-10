import asyncio
from inventory import get_inventory
from execute import execute
from response import validate_responses
from construction_tracker import  track_construction, track_yields
import logging
from commands.apt import Package
from utilities.checks import changed,flatten

@track_construction
class Root:
    @track_yields
    async def execute(self):
        r = yield Package(   name="initial remove package",
                             packages =["tree"],
                             present = False,
                             group="All",
                             sudo=True)
        r = yield Package(   name="install package",
                             packages =["tree"],
                             present = True,
                             group="All",
                             sudo=True)
        for x in flatten(r):
            print(x)
        if changed(r):
            print("changed")

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
    responses = await execute(inventory, lambda: Root())
    assert(changed(responses))


if __name__ == "__main__":
    asyncio.run(main())
