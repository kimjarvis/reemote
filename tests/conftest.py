import asyncio

import pytest

from reemote.core.config import Config
from reemote.execute import endpoint_execute
from reemote.inventory import Inventory, InventoryItem, Connection


@pytest.fixture
def setup_inventory():
    inventory = Inventory(
        hosts=[
            InventoryItem(
                connection=Connection(
                    host="server104", username="user", password="password"
                ),
                groups=["all", "server104"],
            ),
            InventoryItem(
                connection=Connection(
                    host="server105", username="user", password="password"
                ),
                groups=["all", "server105"],
            ),
        ]
    )
    config = Config()
    config.set_inventory(inventory.to_json_serializable())


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
                yield Upload(srcpaths=["tests/testdata"], dstpath=".", recurse=True)

        await endpoint_execute(lambda: Root())

    return asyncio.run(inner_fixture())
