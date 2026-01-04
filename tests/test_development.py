import pytest

from reemote.execute import endpoint_execute


@pytest.mark.asyncio
async def test_isdir(setup_inventory, setup_directory):
    from reemote.sftp import Isdir

    class Root:
        async def execute(self):
            r = yield Isdir(path="testdata/dir_a")
            print(r)

    await endpoint_execute(lambda: Root())
