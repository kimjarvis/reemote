import asyncio
from reemote.execute import execute
from reemote.system import Callback
from reemote.core.command import Command
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

    # examples/accessing_the_inventory_in_a_callback.py
    async def _callback(command: Command):
        return command.inventory_item.connection.username

    class Root:
        async def execute(self):
            response = yield Callback(callback=_callback)
            print(response["value"])

    await execute(lambda: Root(), inventory=inventory)


if __name__ == "__main__":
    asyncio.run(main())
