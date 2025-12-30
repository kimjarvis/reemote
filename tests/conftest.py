import pytest
import pytest_asyncio
import asyncio

import pytest

from reemote.config import Config
from reemote.execute import endpoint_execute
from reemote.inventory import Inventory


@pytest.fixture
def setup_inventory():
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


@pytest.fixture
def setup_directory():
    async def inner_fixture():
        class Root:
            async def execute(self):
                from reemote.facts.sftp import Isdir
                from reemote.commands.sftp import Rmtree
                from reemote.commands.scp import Upload

                r = yield Isdir(path="testdata")
                if r and r["value"]:
                    yield Rmtree(path="testdata")
                yield Upload(srcpaths=["tests/testdata"],dstpath=".",recurse=True)

        await endpoint_execute(lambda: Root())

    return asyncio.run(inner_fixture())
