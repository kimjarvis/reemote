import asyncio
from reemote.inventory import Inventory, InventoryItem
from reemote.execute import execute


from reemote.inventory import Inventory
from reemote.execute import execute, endpoint_execute
from reemote.config import Config


async def main():
    from reemote.commands.server import Shell

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
    config = Config()
    config.set_inventory(inventory.to_json_serializable())

    class Root:
        async def execute(self):
            r = yield Shell(cmd="echo Hello", group="192.168.1.24")
            if r:
                assert r["value"]["stdout"] == "Hello\n"
                assert r["changed"]

    await endpoint_execute(lambda: Root())


if __name__ == "__main__":
    asyncio.run(main())
