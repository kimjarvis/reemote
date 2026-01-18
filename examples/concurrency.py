# examples/concurrency.py
import asyncio
from reemote.execute import execute
from reemote.core import GetFact
from reemote.inventory import Inventory, InventoryItem, Connection
from reemote.core import Return


async def main():
    inventory = Inventory(
        hosts=[
            InventoryItem(
                connection=Connection(
                    host="server104", username="user", password="password"
                ),
                groups=["all"],
            ),
            InventoryItem(
                connection=Connection(
                    host="server105", username="user", password="password"
                ),
                groups=["all"],
            ),
        ]
    )

    import time

    class Root:
        async def execute(self):
            start_time = time.time()
            yield GetFact(cmd="echo Hello")
            yield GetFact(cmd="echo World!")
            end_time = time.time()
            yield Return(value=(start_time, end_time))

    await execute(lambda: GetFact(cmd="echo Ready?"), inventory=inventory)
    responses = await execute(lambda: Root(), inventory=inventory)
    for response in responses:
        print(response["value"])


if __name__ == "__main__":
    asyncio.run(main())
