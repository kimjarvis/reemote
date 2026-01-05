import asyncio
from reemote.execute import execute
from reemote.host import Shell
from reemote.inventory import Inventory, InventoryItem, Connection


async def main():
    inventory = Inventory(
        hosts=[
            InventoryItem(
                connection=Connection(
                    host="server104", username="user", password="password"
                ),
                groups=["all"],
            ),
        ]
    )

    class Child:
        async def execute(self):
            yield Shell(cmd="echo Hello")

    class Root:
        async def execute(self):
            yield Child()
            yield Shell(cmd="echo World!")

    responses = await execute(lambda: Root(), inventory=inventory)
    for response in responses:
        print(response["value"]["stdout"])


if __name__ == "__main__":
    asyncio.run(main())
