import asyncio

import pytest

from reemote.execute import endpoint_execute


@pytest.fixture
def setup_scp_directory():
    async def inner_fixture():
        class Root:
            async def execute(self):
                from reemote.api.scp import Upload
                from reemote.commands.sftp import Rmtree
                from reemote.api.sftp import Isdir

                r = yield Isdir(path="testdata_scp")
                if r and r["value"]:
                    yield Rmtree(path="testdata_scp")
                yield Upload(
                    srcpaths=["tests/testdata"], dstpath="testdata_scp", recurse=True
                )

        await endpoint_execute(lambda: Root())

    return asyncio.run(inner_fixture())


@pytest.mark.asyncio
async def test_download(setup_inventory, setup_scp_directory):
    import os

    from reemote.api.scp import Download

    class Root:
        async def execute(self):
            yield Download(
                srcpaths=["/home/user/testdata_scp/file_b.txt"],
                dstpath="/tmp/",
                group="192.168.1.24",
            )

    file_path = "/tmp/file_b.txt"

    if os.path.exists(file_path):
        os.remove(file_path)
    await endpoint_execute(lambda: Root())
    assert os.path.exists(file_path)


@pytest.mark.asyncio
async def test_copy(setup_inventory, setup_scp_directory):
    from reemote.api.scp import Copy
    from reemote.commands.sftp import Remove
    from reemote.api.sftp import Isfile

    class Root:
        async def execute(self):
            r = yield Isfile(path="/home/user/testdata_scp/file_c.txt")
            if r and r["value"]:
                yield Remove(path="/home/user/testdata_scp/file_c.txt")
            r = yield Copy(
                srcpaths=["/home/user/testdata_scp/file_b.txt"],
                dstpath="/home/user/testdata_scp/file_c.txt",
                group="192.168.1.24",
                dsthost="192.168.1.76",
            )
            if r:
                r1 = yield Isfile(
                    path="/home/user/testdata_scp/file_c.txt", group="192.168.1.76"
                )
                if r1:
                    assert r1["value"]

    await endpoint_execute(lambda: Root())
