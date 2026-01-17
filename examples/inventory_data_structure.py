# examples/inventory_data_structure.py
import asyncio
from reemote.execute import execute
from reemote.core import GetFact
from reemote.inventory import (
    Inventory,
    InventoryItem,
    Connection,
    Session,
    Authentication,
)


async def main():
    inventory = Inventory(
        hosts=[
            InventoryItem(
                connection=Connection(
                    host="server104", username="user", password="password"
                ),
                groups=["all", "servers"],
            ),
            InventoryItem(
                connection=Connection(
                    host="server105", username="user", password="password"
                ),
                session=Session(term_type="xterm"),
                authentication=Authentication(sudo_password="password"),
                groups=["all", "databases"],
            ),
        ]
    )

    responses = await execute(
        lambda: GetFact(cmd="cat /etc/shadow", sudo=True, group="databases"),
        inventory=inventory,
    )
    for response in responses:
        print(response["value"]["stdout"])


if __name__ == "__main__":
    asyncio.run(main())
