import asyncio

import pytest

from reemote.execute import endpoint_execute


@pytest.mark.asyncio
async def test_copy(setup_inventory, setup_directory):
    from reemote.commands.sftp import Copy
    from reemote.api.sftp import Isfile

    class Root:
        async def execute(self):
            yield Copy(srcpaths="testdata/file_b.txt", dstpath="testdata/dir_a")
            r = yield Isfile(path="testdata/dir_a/file_b.txt")
            assert r and r["value"]

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_mcopy(setup_inventory, setup_directory):
    from reemote.commands.sftp import Copy, Mkdir, Mcopy
    from reemote.api.sftp import Isfile

    class Root:
        async def execute(self):
            yield Copy(srcpaths="testdata/file_b.txt", dstpath="testdata/dir_a")
            yield Mkdir(path="testdata/dir_b")
            yield Mcopy(srcpaths="testdata/dir_a/*.txt", dstpath="testdata/dir_b")
            r = yield Isfile(path="testdata/dir_b/file_a.txt")
            assert r and r["value"]
            r = yield Isfile(path="testdata/dir_b/file_b.txt")
            assert r and r["value"]

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_get(setup_inventory, setup_directory):
    from reemote.commands.sftp import Get
    import os

    class Root:
        async def execute(self):
            yield Get(remotepaths="testdata/file_b.txt", localpath="/tmp")
            assert os.path.exists("/tmp/file_b.txt")

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_mget(setup_inventory, setup_directory):
    from reemote.commands.sftp import Mget
    import os

    class Root:
        async def execute(self):
            yield Mget(remotepaths="testdata/dir_a/*.txt", localpath="/tmp")
            assert os.path.exists("/tmp/file_a.txt")

    await endpoint_execute(lambda: Root())


@pytest.mark.asyncio
async def test_put(setup_inventory, setup_directory):
    from reemote.commands.sftp import Put
    from reemote.api.sftp import Isfile

    class Root:
        async def execute(self):
            with open("/tmp/file_c.txt", "w") as file:
                file.write("file_c")
            yield Put(localpaths="/tmp/file_c.txt", remotepath="testdata")
            r = yield Isfile(path="testdata/file_c.txt")
            assert r and r["value"]

    await endpoint_execute(lambda: Root())

@pytest.mark.asyncio
async def test_mput(setup_inventory, setup_directory):
    from reemote.commands.sftp import Mput
    from reemote.api.sftp import Isfile

    class Root:
        async def execute(self):
            with open("/tmp/file_c.txt", "w") as file:
                file.write("file_c")
            with open("/tmp/file_d.txt", "w") as file:
                file.write("file_d")
            yield Mput(localpaths="/tmp/file_*.txt", remotepath="testdata")
            r = yield Isfile(path="testdata/file_c.txt")
            assert r and r["value"]
            r = yield Isfile(path="testdata/file_d.txt")
            assert r and r["value"]

    await endpoint_execute(lambda: Root())
