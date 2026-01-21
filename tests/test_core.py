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
    from reemote.core import return_get
    from reemote.context import Method
    r = await endpoint_execute(lambda: return_get(value=1, group="server104"))
    assert len(r) == 1


@pytest.mark.asyncio
async def test_get_call(setup_inventory):
    from reemote.core.get_call import CallGet
    from reemote.context import Context

    async def callback(context: Context):
        return "tested"

    r = await endpoint_execute(lambda: CallGet(callback=callback, group="server104"))
    print(r)
    assert len(r) == 1
    assert r[0]["value"] == "tested"

@pytest.mark.asyncio
async def test_get_call_ensure_operations_in_callback_fail_part_1(setup_inventory):
    from reemote.core import CallGet
    from reemote.context import Context
    from reemote.sftp1 import IsDir

    async def callback(context: Context):
        # r = yield Isdir(path="/home/user")
        return "tested"

    await endpoint_execute(lambda: CallGet(callback=callback, group="server104"))

@pytest.mark.asyncio
async def test_get_call_ensure_operations_in_callback_fail_part_2(setup_inventory):
    # Ensure that operations cannot be called from within a callback
    invalid_code="""
    from reemote.core import get_call
    from reemote.context import Context
    from reemote.sftp1 import Isdir

    async def callback(context: Context):
        r = yield Isdir(path="/home/user")
        return "tested"

    await endpoint_execute(lambda: Call(callback=get_call,group="server104"))
    """
    with pytest.raises(SyntaxError):
        exec(invalid_code)


@pytest.mark.asyncio
async def test_systemcallback(setup_inventory):
    from reemote.core.get_call import CallGet
    from reemote.context import Context

    async def callback(context: Context):
        return "tested"

    class Root:
        async def execute(self):
            r = yield CallGet(callback=callback)
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
async def test_return_get_4(setup_inventory):
    from reemote.core import return_get
    r = await endpoint_execute(lambda: return_get(value=1))
    assert all(d.get('value') == 1 for d in r), "Not all dictionaries have value"

@pytest.mark.asyncio
async def test_return2(setup_inventory):
    from reemote.core import return_put
    r = await endpoint_execute(lambda: return_put(changed=True))
    assert all(d.get('changed') for d in r), "Not all dictionaries have changed==True"


@pytest.mark.asyncio
async def test_return_use(setup_inventory):
    from reemote.core import return_get
    from reemote.core import GetFact
    from reemote.context import Method

    class Child:
        async def execute(self):
            a = yield GetFact(cmd="echo Hello")
            b = yield GetFact(cmd="echo World")
            yield return_get(value=[a, b])

    class Parent:
        async def execute(self):
            r = yield Child()
            if r:
                assert r["value"][0]["value"]["stdout"] == "Hello\n"
                assert r["value"][1]["value"]["stdout"] == "World\n"

    r = await endpoint_execute(lambda: Parent())
    assert len(r) == 2


@pytest.mark.asyncio
async def test_core_get_call_value_passing(setup_inventory):
    from reemote.core import CallGet
    from reemote.context import Context

    async def _callback(context: Context):
        return context.value+" World!"

    class Root:
        async def execute(self):
            r = yield CallGet(callback=_callback, value="Hello")
            if r:
                assert r["value"] == "Hello World!"

    r = await endpoint_execute(lambda: Root())
    assert len(r) == 2

@pytest.mark.asyncio
async def test_core_put_call(setup_inventory):
    from reemote.core import put_call
    from reemote.context import Context

    async def _callback(context: Context):
        context.changed = False

    class Root:
        async def execute(self):
            r = yield put_call(callback=_callback, value="Hello")
            print(r)
            if r:
                assert not r["changed"]

    r = await endpoint_execute(lambda: Root())
    assert len(r) == 2

@pytest.mark.asyncio
async def test_core_put_call(setup_inventory):
    from reemote.core import post_call
    from reemote.context import Context

    async def _callback(context: Context):
        context.changed = False

    class Root:
        async def execute(self):
            r = yield post_call(callback=_callback, value="Hello")
            print(r)

    r = await endpoint_execute(lambda: Root())
    assert len(r) == 2


