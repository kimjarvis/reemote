# examples/hello_world.py
import asyncio
from reemote.execute import execute
from reemote.core import GetFact
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
        lambda: GetFact(cmd="echo Hello World!"), inventory=inventory
    )
    for response in responses:
        print(response["value"]["stdout"])


if __name__ == "__main__":
    asyncio.run(main())
