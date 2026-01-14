import pytest

from reemote.execute import endpoint_execute




@pytest.mark.asyncio
async def test_isdir_return1(setup_inventory):
    from reemote.sftp import Isdir

    class Root:
        async def execute(self):
            r = yield Isdir(path="/home/user")
            print(r)

    await endpoint_execute(lambda: Root())
