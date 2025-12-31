import asyncio
from reemote.api.inventory import Inventory
from reemote.execute import execute


async def main():
    from reemote.api.server import Shell

    inventory = Inventory(
        hosts=[
            {
                "connection": {
                    "host": "192.168.1.24",
                    "username": "user",
                    "password": "password",
                },
                "host_vars": {"sudo_user": "user"},
                "groups": ["all", "192.168.1.24"],
            },
            {
                "connection": {
                    "host": "192.168.1.76",
                    "username": "user",
                    "password": "password",
                },
                "host_vars": {"sudo_user": "user"},
                "groups": ["all", "192.168.1.76"],
            },
        ]
    )
    r = await execute(lambda: Shell(cmd="pwd"), inventory=inventory, logfile="logging.log")
    print(r)


if __name__ == "__main__":
    asyncio.run(main())
