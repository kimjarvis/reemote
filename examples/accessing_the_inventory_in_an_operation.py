import asyncio
from reemote.execute import execute
from reemote.host import Getcontext
from reemote.inventory import Inventory, InventoryItem, Connection


async def main():
    inventory = Inventory(
        hosts=[
            InventoryItem(
                connection=Connection(
                    host="server104", username="user", password="password"
                ),
            )
        ]
    )

    # examples/accessing_the_inventory_in_an_operation.py
    class Root:
        async def execute(self):
            response = yield Getcontext()
            context = response["value"]
            print(context.inventory_item.connection.username)

    await execute(lambda: Root(), inventory=inventory)


if __name__ == "__main__":
    asyncio.run(main())
