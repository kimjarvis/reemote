import pytest

from reemote.execute import endpoint_execute

@pytest.mark.asyncio
async def test_call_get(setup_inventory):
    from reemote.core.call_get import call_get
    from reemote.context import Context

    async def callback(context: Context):
        return "tested"

    r = await endpoint_execute(lambda: call_get(callback=callback, group="server104"))
    print(r)
    assert len(r) == 1
    assert r[0].value == "tested"

