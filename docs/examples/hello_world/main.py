import asyncio
from reemote.execute import execute
from reemote.api.server import Shell
from reemote.api.inventory import Inventory, InventoryItem, Connection


async def main():
    inventory = Inventory(
        hosts=[
            InventoryItem(
                connection=Connection(
                    host="server104", username="user", password="password"
                ),
            ),
        ]
    )

    responses = await execute(
        lambda: Shell(cmd="echo Hello World!"),
        inventory=inventory,
        logfile="logfile.log",
    )
    for response in responses:
        print(response["value"]["stdout"])


if __name__ == "__main__":
    asyncio.run(main())
