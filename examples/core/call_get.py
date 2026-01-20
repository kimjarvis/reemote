# examples/core/call_get.py
import asyncio
import sys
import os

# Add the parent directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from setup_logging import setup_logging
from reemote.execute import execute
from reemote.inventory import Connection, Inventory, InventoryItem


async def example_core_call_get(inventory):
    from reemote.context import Context
    from reemote import core1

    async def callback(context: Context):
        return context.value + "World!"

    responses = await execute(lambda: core1.CallGet(callback=callback, value="Hello ", group="server104"), inventory)
    for item in responses:
        assert item.value == "Hello World!", "The callback should append 'World!' to the input value"

    return responses

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
    r = await example_core_call_get(inventory)
    responses={
        200: {
            "description": "Successful Response",
            "content": {
                "application/json": {
                    "example": [item.model_dump() for item in r]
                }
            }
        }
    }
    print(responses)

if __name__ == "__main__":
    asyncio.run(main())

