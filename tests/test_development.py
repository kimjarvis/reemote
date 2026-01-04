import pytest

from reemote.execute import endpoint_execute


@pytest.mark.asyncio
async def test_shell(setup_inventory):
    from reemote.shell import Shell

    class Root:
        async def execute(self):
            r = yield Shell(cmd="echo Hello")
            print(r)
            if r:
                assert r["value"]["stdout"] == "Hello\n"
                assert r["changed"]
                assert not r["error"]

    r = await endpoint_execute(lambda: Root())
    assert len(r)==2
