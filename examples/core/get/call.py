# examples/core/call.py
import asyncio
import os
import sys

# Add the grandparent directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from setup_logging import setup_logging

from reemote.execute import execute
from reemote.inventory import Connection, Inventory, InventoryItem


async def example_core_get_call(inventory):
    from reemote import core1
    from reemote.context import Context

    async def callback(context: Context):
        return context.value + "World!"

    responses = await execute(lambda: core1.get.Call(callback=callback, value="Hello ", group="server104"), inventory)
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
    r = await example_core_get_call(inventory)
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

