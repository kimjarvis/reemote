# examples/compostion.py
import asyncio
from reemote.execute import execute
from reemote.core import GetFact
from reemote.inventory import Inventory, InventoryItem, Connection
from reemote.core import ReturnPut
from reemote.context import Method
from setup_logging import setup_logging


async def main():
    inventory = Inventory(
        hosts=[
            InventoryItem(
                connection=Connection(
                    host="server104", username="user", password="password"
                ),
            ),
        ]
    )

    class Child:
        async def execute(self):
            yield GetFact(cmd="echo Hello")

    class Root:
        async def execute(self):
            hello_response = yield Child()
            world_response = yield GetFact(cmd="echo World!")
            yield ReturnPut(
                method=Method.GET,
                value=hello_response["value"]["stdout"]
                + world_response["value"]["stdout"],
            )

    setup_logging()

    responses = await execute(lambda: Root(), inventory=inventory)
    for response in responses:
        print(response["value"])


if __name__ == "__main__":
    asyncio.run(main())
