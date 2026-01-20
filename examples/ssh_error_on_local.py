# examples/ssh_error_on_request.py
import asyncio
from reemote.execute import execute
from reemote.sftp1 import IsDir
from reemote.inventory import Inventory, InventoryItem, Connection
import logging

async def main():
    inventory = Inventory(
        hosts=[
            InventoryItem(
                connection=Connection(
                    host="server104", username="user", password="password"
                )
            ),
            InventoryItem(
                connection=Connection(
                    host="server105", username="user", password="password"
                )
            ),
            InventoryItem(
                connection=Connection(
                    host="unreachablehost", username="user", password="password"
                )
            ),
        ]
    )

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG,
        filename="reemote.log",
        filemode="w",  # Overwrite the file each time
        format="%(asctime)s - %(name)s - %(levelname)-8s - %(message)s",
    )
    # Suppress asyncssh logs by setting its log level to WARNING or higher
    logging.getLogger("asyncssh").setLevel(logging.WARNING)


    responses = await execute(
        lambda: IsDir(path="/home/user"), inventory=inventory
    )
    print(responses)

if __name__ == "__main__":
    asyncio.run(main())
