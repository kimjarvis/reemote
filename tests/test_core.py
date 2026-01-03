import pytest

from reemote.execute import endpoint_execute


@pytest.mark.asyncio
async def test_core_group(setup_inventory):
    from reemote.shell import Shell

    class Root:
        async def execute(self):
            r = yield Shell(cmd="echo Hello",group="server104")
            print(r)
            if r:
                assert r["value"]["stdout"] == "Hello\n"
                assert r["changed"]
                assert not r["error"]

    r = await endpoint_execute(lambda: Root())
    assert len([item for item in r if item is not None])==1