# examples/core/call_post.py
import asyncio
import sys
import os

# Add the parent directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from setup_logging import setup_logging
from reemote.execute import execute
from reemote.inventory import Connection, Inventory, InventoryItem


async def example_core_call_post(inventory):
    from reemote.context import Context
    from reemote import core1

    async def callback(context: Context):
        pass

    responses = await execute(lambda: core1.CallPost(callback=callback, value="start", group="server104"), inventory)

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
    r = await example_core_call_post(inventory)
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

