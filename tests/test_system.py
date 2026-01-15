import pytest

from reemote.execute import endpoint_execute
from reemote.context import Method

@pytest.mark.asyncio
async def test_systemcallback(setup_inventory):
    from reemote.system import Call
    from reemote.context import Context

    async def callback(context: Context):
        return "tested"

    class Root:
        async def execute(self):
            r = yield Call(callback=callback)
            if r:
                assert r["value"] == "tested"
                assert r["changed"]

    r = await endpoint_execute(lambda: Root())
    assert len(r) == 2

@pytest.mark.asyncio
async def test_return_get(setup_inventory):
    from reemote.system import Return
    r = await endpoint_execute(lambda: Return(method=Method.GET, value=1))
    assert all(d.get('value') == 1 for d in r), "Not all dictionaries have value"

@pytest.mark.asyncio
async def test_return_get1(setup_inventory):
    from reemote.system import Return
    from reemote.context import Method
    r = await endpoint_execute(lambda: Return(method=Method.PUT, changed=True))
    assert all(d.get('changed') for d in r), "Not all dictionaries have changed==True"


@pytest.mark.asyncio
async def test_return_use(setup_inventory):
    from reemote.system import Return
    from reemote.host import Shell

    class Child:
        async def execute(self):
            a = yield Shell(cmd="echo Hello")
            b = yield Shell(cmd="echo World")
            yield Return(value=[a, b], method=Method.GET)

    class Parent:
        async def execute(self):
            r = yield Child()
            if r:
                assert r["value"][0]["value"]["stdout"] == "Hello\n"
                assert r["value"][1]["value"]["stdout"] == "World\n"

    r = await endpoint_execute(lambda: Parent())
    assert len(r) == 2
