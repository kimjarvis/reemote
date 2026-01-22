# examples/sftp/IsDir.py
import asyncio
import sys
import os

# Add the parent directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from setup_logging import setup_logging
from reemote.execute import execute
from reemote.inventory import Connection, Inventory, InventoryItem


async def example_sftp_is_dir(inventory):
    from reemote import sftp1

    responses = await execute(lambda: sftp1.get.IsDir(path=".."), inventory)
    for item in responses:
        assert item.value, "The coroutine should report that the current working directory exists on the host."

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
    r = await example_sftp_is_dir(inventory)
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

