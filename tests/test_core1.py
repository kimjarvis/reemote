import pytest

from reemote.execute import endpoint_execute


@pytest.mark.asyncio
async def test_call_get(setup_inventory):
    from reemote.context import Context
    from reemote.core.call_get import CallGet

    async def callback(context: Context):
        return context.value + "World!"

    responses = await endpoint_execute(lambda: CallGet(callback=callback, value="Hello ", group="server104",))
    print(responses)
    assert len(responses) == 1
    for item in responses:
        assert item.value == "Hello World!"