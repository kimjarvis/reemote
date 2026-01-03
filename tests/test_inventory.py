import asyncio

import pytest

from reemote.core.config import Config
from reemote.execute import endpoint_execute
from reemote.inventory import Inventory


@pytest.fixture(autouse=True)
def setup_inventory():
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
                    "host": "server104",
                    "username": "user",
                    "password": "password",
                },
                "host_vars": {"sudo_user": "user"},
                "groups": ["all", "server104"],
            },
        ]
    )
    config = Config()
    config.set_inventory(inventory.to_json_serializable())


@pytest.mark.asyncio
async def test_unreachable_host_sftp_command(setup_inventory, setup_directory):
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



@pytest.fixture
def setup_directory():
    async def inner_fixture():
        class Root:
            async def execute(self):
                from reemote.sftp import Isdir
                from reemote.sftp import Rmtree
                from reemote.scp import Upload

                r = yield Isdir(path="testdata")
                if r and r["value"]:
                    yield Rmtree(path="testdata")
                yield Upload(srcpaths=["tests/testdata"],dstpath=".",recurse=True)

        await endpoint_execute(lambda: Root())

    return asyncio.run(inner_fixture())


@pytest.mark.asyncio
async def test_unreachable_host_sftp_fact(setup_inventory, setup_directory):
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

