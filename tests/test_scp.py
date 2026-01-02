import os
import asyncio

import pytest

from reemote.execute import endpoint_execute


@pytest.mark.asyncio
async def test_download(setup_inventory, setup_directory):
    from reemote.api.scp import Download

    class Root:
        async def execute(self):
            yield Download(
                srcpaths=["/home/user/testdata/file_b.txt"],
                dstpath="/tmp/",
                group="server105",
            )

    file_path = "/tmp/file_b.txt"

    if os.path.exists(file_path):
        os.remove(file_path)
    await endpoint_execute(lambda: Root())
    assert os.path.exists(file_path)


@pytest.mark.asyncio
async def test_copy(setup_inventory, setup_directory):
    from reemote.api.scp import Copy
    from reemote.api.sftp import Remove
    from reemote.api.sftp import Isfile

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
                r1 = yield Isfile(
                    path="/home/user/testdata/file_c.txt", group="server104"
                )
                if r1:
                    assert r1["value"]

    await endpoint_execute(lambda: Root())
