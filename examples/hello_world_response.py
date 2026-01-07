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
            InventoryItem(
                connection=Connection(
                    host="server105", username="user", password="password"
                )
            ),
        ]
    )

    # examples/hello_world_response.py
    responses = await execute(
        lambda: Shell(cmd="echo Hello World!"), inventory=inventory
    )
    # Convert to formatted JSON
    import json
    formatted_json = json.dumps(responses, indent=4)
    print(formatted_json)

if __name__ == "__main__":
    asyncio.run(main())
