import pytest

from reemote.execute import endpoint_execute


@pytest.mark.asyncio
async def test_shell(setup_inventory):
    from reemote.host import Shell

    class Root:
        async def execute(self):
            r = yield Shell(cmd="echo Hello")
            if r:
                assert r["value"]["stdout"] == "Hello\n"
                assert r["changed"]
                assert not r["error"]

    r = await endpoint_execute(lambda: Root())
    assert len(r)==2

@pytest.mark.asyncio
async def test_shell_sudo(setup_inventory):
    from reemote.host import Shell

    class Root:
        async def execute(self):
            r = yield Shell(cmd="ls /root",sudo=True)
            if r:
                assert not r["error"]

    await endpoint_execute(lambda: Root())

@pytest.mark.asyncio
async def test_get_context(setup_inventory):
    from reemote.host import Getcontext

    class Root:
        async def execute(self):
            r = yield Getcontext()
            if r:
                assert not r["error"]

    r = await endpoint_execute(lambda: Root())
    assert len(r)==2
