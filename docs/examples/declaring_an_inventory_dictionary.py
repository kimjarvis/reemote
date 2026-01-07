import asyncio
from reemote.execute import execute
from reemote.host import Shell
from reemote.inventory import Inventory


async def main():
    # examples/declaring_an_inventory_dictionary.py
    inventory = Inventory(
        hosts=[
            {
                "connection": {
                    "host": "server104",
                    "username": "user",
                    "password": "password",
                },
            }
        ]
    )

    responses = await execute(
        lambda: Shell(cmd="echo Hello World!"),
        inventory=inventory,
    )
    for response in responses:
        print(response["value"]["stdout"])


if __name__ == "__main__":
    asyncio.run(main())
