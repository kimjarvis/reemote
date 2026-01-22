# examples/sftp/IsDir.py
import asyncio
import sys
import os

from scripts.generate_example import generate_example
from scripts.generate_responses import generate_responses

# Add the parent directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.setup_logging import setup_logging
from reemote.execute import execute
from reemote.inventory import Connection, Inventory, InventoryItem


async def example(inventory):
    from reemote import sftp1

    responses = await execute(lambda: sftp1.get.IsDir(path=".."), inventory)
    for item in responses:
        assert item.value, (
            "The coroutine should report that the current working directory exists on the host."
        )

    return responses


async def main():
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

    filename_no_ext = os.path.splitext(os.path.basename(__file__))[0]
    # Call the function to write the body to a flat file
    generate_example(example, filename=f"{filename_no_ext}_example.py")
    # Generate the responses.txt file
    generate_responses(responses, filename=f"{filename_no_ext}_example_responses.py")

    print(responses)


if __name__ == "__main__":
    asyncio.run(main())
