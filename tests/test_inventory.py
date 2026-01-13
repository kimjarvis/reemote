import pytest
from typing import AsyncGenerator

from reemote.config import Config
from reemote.execute import endpoint_execute
from reemote.inventory import Inventory
from reemote.inventory import InventoryItem, Connection, Session
from reemote.context import Context
from reemote.core.response import ResponseModel

@pytest.mark.asyncio
async def test_inventory_get():
    from reemote.inventory import Getinventory

    class Root:
        async def execute(self):
            r = yield Getinventory()
            if r:
                assert any(
                    host['connection']['host'] == 'server104' for host in r['value']['hosts']), "server104 not found"
                assert any(
                    host['connection']['host'] == 'server105' for host in r['value']['hosts']), "server105 not found"

    r = await endpoint_execute(lambda: Root())
    assert len(r) == 2

@pytest.mark.asyncio
async def test_inventory_environment_variables(setup_inventory):
    from reemote.host import Shell

    class Root:
        async def execute(self):
            r = yield Shell(cmd="echo $TESTVAR")
            print(r)

    inventory = Inventory(
        hosts=[
            InventoryItem(
                connection=Connection(
                    host="server104", username="user", password="password"
                ),
                session=Session(env={"TESTVAR" : "test"}),
                groups=["all"],
            ),
            InventoryItem(
                connection=Connection(
                    host="server105", username="user", password="password"
                ),
                session=Session(env={"TESTVAR": "test"}),
                groups=["all"],
            ),
        ]
    )
    config = Config()
    config.set_inventory(inventory.to_json_serializable())

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_connection_error():
    from reemote.apt import Update

    class Root:
        async def execute(self) -> AsyncGenerator[Context, ResponseModel]:
            r = yield Update(group="all", name=None, sudo=False, su=False)

    inventory = Inventory(
        hosts=[
            {
                "connection": {
                    "host": "server105",
                    "username": "user",
                    "password": "password",
                },
                "groups": ["all", "server105"],
            },
            {
                "connection": {
                    "host": "unreachablehost",
                    "username": "user",
                    "password": "password",
                },
                "groups": ["all", "unreachablehost"],
            },
        ]
    )
    config = Config()
    config.set_inventory(inventory.to_json_serializable())

    rl = await endpoint_execute(lambda: Root())
    print(rl)
    assert any("error" in r for r in rl)

@pytest.mark.asyncio
async def test_inventory_unreachablehost_host_sftp_command():
    from reemote.sftp import Isdir
    from reemote.sftp import Mkdir, Rmdir

    class Root:
        async def execute(self):
            r = yield Isdir(path="/home/user/dir_e")
            if r and r["value"]:
                yield Rmdir(path="/home/user/dir_e")
            yield Mkdir(path="/home/user/dir_e")

    inventory = Inventory(
        hosts=[
            {
                "connection": {
                    "host": "server105",
                    "username": "user",
                    "password": "password",
                },
                "groups": ["all", "server105"],
            },
            {
                "connection": {
                    "host": "unreachablehost",
                    "username": "user",
                    "password": "password",
                },
                "groups": ["all", "unreachablehost"],
            },
        ]
    )
    config = Config()
    config.set_inventory(inventory.to_json_serializable())

    rl = await endpoint_execute(lambda: Root())
    assert any("error" in r for r in rl)


@pytest.mark.asyncio
async def test_inventory_unreachablehost_host_sftp_fact():
    from reemote.sftp import StatVfs

    class Root:
        async def execute(self):
            r = yield StatVfs(path="testdata/dir_a")

    inventory = Inventory(
        hosts=[
            {
                "connection": {
                    "host": "server105",
                    "username": "user",
                    "password": "password",
                },
                "groups": ["all", "server105"],
            },
            {
                "connection": {
                    "host": "unreachablehost",
                    "username": "user",
                    "password": "password",
                },
                "groups": ["all", "unreachablehost"],
            },
        ]
    )
    config = Config()
    config.set_inventory(inventory.to_json_serializable())

    rl = await endpoint_execute(lambda: Root())
    assert any("error" in r for r in rl)
