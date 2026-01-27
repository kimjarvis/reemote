# block extract examples/hello_world.py.generated
# examples/hello_world.py
import asyncio

from reemote import core
from reemote.execute import execute
from reemote.inventory import Connection, Inventory, InventoryItem


async def main():
    inventory = Inventory(
        hosts=[
            InventoryItem(
                connection=Connection(
                    host="server104", username="user", password="password"
                )
            ),
            InventoryItem(
                connection=Connection(
                    host="server999", username="user", password="password"
                )
            ),
        ]
    )

    responses = await execute(
        lambda: core.get.Fact(cmd="echo Hello World!"), inventory=inventory
    )
    # for response in responses:
    #     assert  "Hello World!" in response.value.stdout, (
    #         "Expected the each host to return 'Hello World!'"
    #     )


if __name__ == "__main__":
    asyncio.run(main())
# block end
