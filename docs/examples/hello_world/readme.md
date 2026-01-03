# Hello world example

These examples demonstrate the execution of a shell command on a server and the collection of the results.

```python
import asyncio
from reemote.execute import execute
from reemote.shell import Shell
from reemote.inventory import Inventory, InventoryItem, Connection


async def main():
    inventory = Inventory(
        hosts=[
            InventoryItem(
                connection=Connection(
                    host="server104", username="user", password="password"
                ),
                groups=["all"],
            ),
        ]
    )

    responses = await execute(
        lambda: Shell(cmd="echo Hello World!"),
        inventory=inventory,
        logfile="logfile.log",
    )
    for response in responses:
        print(response["value"]["stdout"])


if __name__ == "__main__":
    asyncio.run(main())
```










