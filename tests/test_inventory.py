import asyncio

import pytest

from reemote.core.config import Config
from reemote.execute import endpoint_execute
from reemote.inventory import Inventory

@pytest.mark.asyncio
async def test_get_inventory():
    from reemote.inventory import Getinventory

    class Root:
        async def execute(self):
            r = yield Getinventory()
            print(f"debug 00 {r}")
            print(f"debug 01 {r["value"]}")
            print(f"debug 02 {type(r["value"]["hosts"])}")

            if r:
                assert any(
                    host['connection']['host'] == 'server104' for host in r['value']['hosts']), "server104 not found"
                assert any(
                    host['connection']['host'] == 'server105' for host in r['value']['hosts']), "server105 not found"

    r = await endpoint_execute(lambda: Root())
    assert len(r) == 2


@pytest.mark.asyncio
async def test_inventory_unreachable_host_sftp_command():
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
                "host_vars": {"sudo_user": "user"},
                "groups": ["all", "server105"],
            },
            {
                "connection": {
                    "host": "192.168.1.1",
                    "username": "user",
                    "password": "password",
                },
                "host_vars": {"sudo_user": "user"},
                "groups": ["all", "192.168.1.1"],
            },
        ]
    )
    config = Config()
    config.set_inventory(inventory.to_json_serializable())

    rl = await endpoint_execute(lambda: Root())
    assert any("error" in r for r in rl)


@pytest.mark.asyncio
async def test_inventory_unreachable_host_sftp_fact():
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
                "host_vars": {"sudo_user": "user"},
                "groups": ["all", "server105"],
            },
            {
                "connection": {
                    "host": "192.168.1.1",
                    "username": "user",
                    "password": "password",
                },
                "host_vars": {"sudo_user": "user"},
                "groups": ["all", "192.168.1.1"],
            },
        ]
    )
    config = Config()
    config.set_inventory(inventory.to_json_serializable())

    rl = await endpoint_execute(lambda: Root())
    assert any("error" in r for r in rl)
