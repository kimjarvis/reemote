import asyncio
import pytest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from reemote.inventory import Inventory
from reemote.execute import endpoint_execute
from reemote.config import Config


# Autouse fixture that runs before each test
@pytest.fixture(autouse=True)
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


@pytest.mark.asyncio
async def test_download(setup_inventory,setup_directory):
    import os
    from reemote.commands.scp import Download

    class Root:
        async def execute(self):
            yield Download(srcpaths=["/home/user/testdata/file_b.txt"], dstpath="/tmp/",group="192.168.1.24")

    file_path = "/tmp/file_b.txt"

    if os.path.exists(file_path):
        os.remove(file_path)
    await endpoint_execute(lambda: Root())
    assert os.path.exists(file_path)

@pytest.mark.asyncio
async def test_copy(setup_inventory, setup_directory):
    from reemote.commands.scp import Copy
    from reemote.commands.sftp import Remove
    from reemote.facts.sftp import Isfile

    class Root:
        async def execute(self):
            r = yield Isfile(path="/home/user/testdata/file_c.txt")
            if r and r["value"]:
                yield Remove(path="/home/user/testdata/file_c.txt")
            r = yield Copy(srcpaths=["/home/user/testdata/file_b.txt"], dstpath="/home/user/testdata/file_c.txt",group="192.168.1.24",dsthost="192.168.1.76")
            if r:
                r1 = yield Isfile(path="/home/user/testdata/file_c.txt",group="192.168.1.76")
                if r1:
                    assert r1["value"]

    await endpoint_execute(lambda: Root())
