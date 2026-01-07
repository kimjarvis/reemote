import pytest

from reemote.execute import endpoint_execute


@pytest.mark.asyncio
async def test_system_callback(setup_inventory):
    from reemote.system import Callback
    from reemote.context import Context

    async def _callback(context: Context):
        return "tested"

    class Root:
        async def execute(self):
            r = yield Callback(callback=_callback)
            if r:
                assert r["value"] == "tested"
                assert r["changed"]

    r = await endpoint_execute(lambda: Root())
    assert len(r) == 2


@pytest.mark.asyncio
async def test_system_callback(setup_inventory):
    from reemote.system import Callback
    from reemote.context import Context

    async def _callback(command: Context):
        assert command.value == "test callback"
        return "tested"

    class Root:
        async def execute(self):
            r = yield Callback(callback=_callback, value="test callback")
            if r:
                assert r["value"] == "tested"
                assert r["changed"]

    r = await endpoint_execute(lambda: Root())
    assert len(r) == 2

@pytest.mark.asyncio
async def test_return(setup_inventory):
    from reemote.system import Return
    from reemote.host import Shell

    class Child:
        async def execute(self):
            a = yield Shell(cmd="echo Hello")
            b = yield Shell(cmd="echo World")
            yield Return(value=[a, b], changed=False)

    class Parent:
        async def execute(self):
            r = yield Child()
            if r:
                assert r["value"][0]["value"]["stdout"] == "Hello\n"
                assert r["value"][1]["value"]["stdout"] == "World\n"

    r = await endpoint_execute(lambda: Parent())
    assert len(r) == 2
