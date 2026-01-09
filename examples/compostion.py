# examples/compostion.py
import asyncio
from reemote.execute import execute
from reemote.host import Shell
from reemote.inventory import Inventory, InventoryItem, Connection
from reemote.system import Return


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
            yield Shell(cmd="echo Hello")

    class Root:
        async def execute(self):
            hello_response = yield Child()
            world_response = yield Shell(cmd="echo World!")
            yield Return(value=hello_response["value"]["stdout"] + world_response["value"]["stdout"])


    responses = await execute(lambda: Root(), inventory=inventory)
    for response in responses:
        print(response["value"])


if __name__ == "__main__":
    asyncio.run(main())
