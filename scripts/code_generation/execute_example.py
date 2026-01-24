from typing import Any, Callable

from reemote.inventory import Connection, Inventory, InventoryItem
from scripts.setup_logging import setup_logging


async def execute_example(example: Callable ) -> dict[
    int, dict[str, str | dict[str, dict[str, list[Any]]]]
]:
    inventory = Inventory(
        hosts=[
            InventoryItem(
                connection=Connection(
                    host="server104", username="user", password="password"
                ),
                groups=["server104"],
            ),
            InventoryItem(
                connection=Connection(
                    host="server105", username="user", password="password"
                ),
                groups=["server105"],
            ),
        ]
    )
    setup_logging()

    # Execute the sftp_IsDir function
    r = await example(inventory)

    # Prepare the responses dictionary
    responses = {
        200: {
            "description": "Successful Response",
            "content": {
                "application/json": {"sftp_IsDir": [item.model_dump() for item in r]}
            },
        }
    }
    return responses
