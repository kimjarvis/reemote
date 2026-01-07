import asyncio
from reemote.execute import execute
from reemote.host import Shell
from reemote.inventory import Inventory, InventoryItem, Connection


async def main():
    inventory = Inventory(
        hosts=[
            InventoryItem(
                connection=Connection(
                    host="server104", username="user", password="password"
                )
            ),
        ]
    )

    # examples/accessing_the_host_name_or_address.py
    class Root:
        async def execute(self):
            response = yield Shell(cmd="echo Hello World!")
            print(response["host"])

    await execute(lambda: Root(), inventory=inventory)


if __name__ == "__main__":
    asyncio.run(main())
