# examples/core.py
import asyncio

from setup_logging import setup_logging

from reemote.execute import execute
from reemote.inventory import Connection, Inventory, InventoryItem


async def demonstrate_call_get(inventory):
    from reemote.context import Context
    # from reemote.core.call_get import CallGet
    from reemote.core import call_get

    async def callback(context: Context):
        return context.value + "World!"

    responses = await execute(lambda: call_get.CallGet(callback=callback, value="Hello ", group="server104"), inventory)
    print(type( responses[0]))
    for item in responses:
        assert item.value == "Hello World!"

async def main():
    inventory = Inventory(
        hosts=[
            InventoryItem(
                connection=Connection(
                    host="server104", username="user", password="password"
                ),
                groups=["server104"]
            ),
            InventoryItem(
                connection=Connection(
                    host="server105", username="user", password="password"
                ),
                groups=["server105"]
            ),
        ]
    )
    setup_logging()
    await demonstrate_call_get(inventory)

if __name__ == "__main__":
    asyncio.run(main())

