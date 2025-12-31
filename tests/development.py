import pytest

from reemote.execute import endpoint_execute

@pytest.mark.asyncio
async def test_return(setup_inventory):
    from reemote.commands.system import Return
    from reemote.commands.server import Shell

    class Child:
        async def execute(self):
            a = yield Shell(cmd="echo Hello")
            b = yield Shell(cmd="echo World")
            yield Return(value=[a, b],changed=True)

    class Parent:
        async def execute(self):
            r = yield Child()
            if r:
                assert r["value"][0]["value"]["stdout"] == 'Hello\n'
                assert r["value"][1]["value"]["stdout"] == 'World\n'
                assert r["changed"]

    await endpoint_execute(lambda: Parent())
