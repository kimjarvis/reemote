import pytest

from reemote.execute import endpoint_execute


@pytest.mark.asyncio
async def test_development(setup_inventory):
    from reemote.shell import Shell

    class Root:
        async def execute(self):
            r = yield Shell(cmd="echo Hello")
            print(r)

    await endpoint_execute(lambda: Root())
