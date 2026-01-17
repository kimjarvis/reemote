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
    from reemote.host import Getcontext

    class Root:
        async def execute(self):
            r = yield Getcontext()
            if r:
                assert not r["error"]
            print(r)

    r = await endpoint_execute(lambda: Root())
    assert len(r)==2


@pytest.mark.asyncio
async def test_return1(setup_inventory):
    from reemote.system import Return
    from reemote.context import Method
    r = await endpoint_execute(lambda: Return(method=Method.GET, value=1, group="server104"))
    assert len(r) == 1


@pytest.mark.asyncio
async def test_call1(setup_inventory):
    from reemote.system import Call
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
