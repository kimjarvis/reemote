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
                    host="server105", username="user", password="password"
                )
            ),
        ]
    )

    # examples/hello_world_response.py
    responses = await execute(
        lambda: core.get.Fact(cmd="echo Hello World!"), inventory=inventory
    )
    print(core.get.Fact.Responses.model_validate(responses).model_dump_json(indent=4))

if __name__ == "__main__":
    asyncio.run(main())
