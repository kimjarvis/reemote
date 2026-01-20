import os

import pytest

from reemote.execute import endpoint_execute

@pytest.mark.asyncio
async def test_scp_upload():
    from reemote.sftp1 import IsDir
    from reemote.sftp import Rmtree
    from reemote.scp import Upload

    class Root:
        async def execute(self):
            r = yield IsDir(path="testdata")
            if r and r["value"]:
                yield Rmtree(path="testdata")
            yield Upload(srcpaths=["tests/testdata"], dstpath=".", recurse=True)

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_scp_download(setup_inventory, setup_directory):
    from reemote.scp import Download

    class Root:
        async def execute(self):
            r = yield Download(
                srcpaths=["/home/user/testdata/file_b.txt"],
                dstpath="/tmp/",
                group="server105",
            )
            if r:
                assert not r["error"]

    file_path = "/tmp/file_b.txt"

    if os.path.exists(file_path):
        os.remove(file_path)
    r = await endpoint_execute(lambda: Root())
    assert len([item for item in r if item is not None]) == 1
    assert os.path.exists(file_path)


@pytest.mark.asyncio
async def test_scp_copy(setup_inventory, setup_directory):
    from reemote.scp import Copy
    from reemote.sftp import Remove
    from reemote.sftp import Isfile

    class Root:
        async def execute(self):
            r = yield Isfile(path="/home/user/testdata/file_c.txt")
            if r and r["value"]:
                yield Remove(path="/home/user/testdata/file_c.txt")
            r = yield Copy(
                srcpaths=["/home/user/testdata/file_b.txt"],
                dstpath="/home/user/testdata/file_c.txt",
                group="server105",
                dsthost="server104",
            )
            if r:
                assert not r["error"]
                r1 = yield Isfile(
                    path="/home/user/testdata/file_c.txt", group="server104"
                )
                if r1:
                    assert r1["value"]

    await endpoint_execute(lambda: Root())
