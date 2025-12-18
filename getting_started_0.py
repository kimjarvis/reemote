import asyncio
from reemote.execute import execute
from reemote.commands.server import Shell
from reemote.inventory import create_inventory


async def main():
    create_inventory([
        [
            {
                "host": "192.168.1.24",
                "username": "user",
                "password": "password"
            },
            {
                "groups": [
                    "all",
                ],
            }
        ],
        [
            {
                "host": "192.168.1.76",
                "username": "user",
                "password": "password"
            },
            {
                "groups": [
                    "all",
                ],
            }
        ],
    ])

    response = await execute(lambda: Shell(cmd="echo Hello World!"))
    for r in response:
        print(r.stdout)

if __name__ == "__main__":
    asyncio.run(main())