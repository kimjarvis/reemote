# examples/hello_world.py
import asyncio
from reemote.execute import execute
from reemote.sftp1 import Is_dir
from reemote.inventory import Inventory, InventoryItem, Connection


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
        ]
    )

    responses = await execute(
        lambda: Is_dir(path="/home/user/fjfj/hh"), inventory=inventory
    )
    print(responses)


if __name__ == "__main__":
    asyncio.run(main())
