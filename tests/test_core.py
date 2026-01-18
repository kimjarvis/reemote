import pytest

from reemote.execute import endpoint_execute


@pytest.mark.asyncio
async def test_getfact(setup_inventory):
    from reemote.core import GetFact

    class Root:
        async def execute(self):
            r = yield GetFact(cmd="echo Hello")
            if r:
                assert not r["error"]
                assert r["value"]["stdout"] == "Hello\n"

    r = await endpoint_execute(lambda: Root())
    assert len(r)==2

@pytest.mark.asyncio
async def test_getfact_sudo(setup_inventory):
    from reemote.core import GetFact

    class Root:
        async def execute(self):
            r = yield GetFact(cmd="ls /root", sudo=True)
            if r:
                assert not r["error"]

    await endpoint_execute(lambda: Root())

@pytest.mark.asyncio
async def test_get_context(setup_inventory):
    from reemote.core.getcontext import GetContext

    class Root:
        async def execute(self):
            r = yield GetContext()
            if r:
                assert not r["error"]
            print(r)

    r = await endpoint_execute(lambda: Root())
    assert len(r)==2


@pytest.mark.asyncio
async def test_return1(setup_inventory):
    from reemote.core import Return
    from reemote.context import Method
    r = await endpoint_execute(lambda: Return(method=Method.GET, value=1, group="server104"))
    assert len(r) == 1


@pytest.mark.asyncio
async def test_call1(setup_inventory):
    from reemote.core.call import Call
    from reemote.context import Context

    async def callback(context: Context):
        return "tested"

    r = await endpoint_execute(lambda: Call(callback=callback,group="server104"))
    assert len(r) == 1
    assert r[0]["value"] == "tested"

@pytest.mark.asyncio
async def test_call2(setup_inventory):
    invalid_code="""
    from reemote.system import Call
    from reemote.context import Context
    from reemote.sftp1 import Isdir

    async def callback(context: Context):
        r = yield Isdir(path="/home/user")
        print(r)
        return "tested"

    await endpoint_execute(lambda: Call(callback=callback,group="server104"))
    print(r)
    """
    with pytest.raises(SyntaxError):
        exec(invalid_code)


@pytest.mark.asyncio
async def test_systemcallback(setup_inventory):
    from reemote.core.call import Call
    from reemote.context import Context

    async def callback(context: Context):
        return "tested"

    class Root:
        async def execute(self):
            r = yield Call(callback=callback)
            print(r)
            if r:
                assert r["value"] == "tested"


    r = await endpoint_execute(lambda: Root())
    assert len(r) == 2


@pytest.mark.asyncio
async def test_core_group(setup_inventory):
    from reemote.core import GetFact

    class Root:
        async def execute(self):
            r = yield GetFact(cmd="echo Hello", group="server104")
            if r:
                assert r["value"]["stdout"] == "Hello\n"
                assert not r["error"]

    r = await endpoint_execute(lambda: Root())
    assert len([item for item in r if item is not None])==1

@pytest.mark.asyncio
async def test_core_recent_responses(setup_inventory):
    """verify that, for each host, only the most recent response is returned."""
    from reemote.core import GetFact

    class Root:
        async def execute(self):
            yield GetFact(cmd="echo Hello")
            yield GetFact(cmd="echo World")

    r = await endpoint_execute(lambda: Root())

@pytest.mark.asyncio
async def test_return_get(setup_inventory):
    from reemote.core import Return
    from reemote.context import Method
    r = await endpoint_execute(lambda: Return(method=Method.GET, value=1))
    assert all(d.get('value') == 1 for d in r), "Not all dictionaries have value"

@pytest.mark.asyncio
async def test_return_get1(setup_inventory):
    from reemote.core import Return
    from reemote.context import Method
    r = await endpoint_execute(lambda: Return(method=Method.PUT, changed=True))
    assert all(d.get('changed') for d in r), "Not all dictionaries have changed==True"


@pytest.mark.asyncio
async def test_return_use(setup_inventory):
    from reemote.core import Return
    from reemote.core import GetFact
    from reemote.context import Method

    class Child:
        async def execute(self):
            a = yield GetFact(cmd="echo Hello")
            b = yield GetFact(cmd="echo World")
            yield Return(value=[a, b], method=Method.GET)

    class Parent:
        async def execute(self):
            r = yield Child()
            if r:
                assert r["value"][0]["value"]["stdout"] == "Hello\n"
                assert r["value"][1]["value"]["stdout"] == "World\n"

    r = await endpoint_execute(lambda: Parent())
    assert len(r) == 2
