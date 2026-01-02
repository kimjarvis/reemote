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
                groups=["all"],
            ),
            InventoryItem(
                connection=Connection(
                    host="server105", username="user", password="password"
                ),
                groups=["all"],
            ),
        ]
    )

    class First:
        async def execute(self):
            yield Shell(cmd="echo Hello")
            yield Shell(cmd="echo World!")

    class Second:
        async def execute(self):
            yield Shell(cmd="echo Bye!!!")

    responses = await execute(
        lambda: First(), inventory=inventory, logfile="logfile.log"
    )
    responses.extend(
        await execute(lambda: Second(), inventory=inventory, logfile="logfile.log")
    )
    for response in responses:
        print(response["value"]["stdout"])


if __name__ == "__main__":
    asyncio.run(main())
