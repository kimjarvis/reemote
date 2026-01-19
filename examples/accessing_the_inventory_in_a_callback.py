import asyncio
from reemote.execute import execute
from reemote.core.call_get1 import call_get
from reemote.context import Context
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

    # examples/accessing_the_inventory_in_acallback.py
    async def callback(context: Context):
        return context.inventory_item.connection.username

    class Root:
        async def execute(self):
            response = yield call_get(callback=callback)
            print(response["value"])

    await execute(lambda: Root(), inventory=inventory)


if __name__ == "__main__":
    asyncio.run(main())
