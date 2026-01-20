import pytest

from reemote.execute import endpoint_execute


@pytest.mark.asyncio
async def test_call_get(setup_inventory):
    from reemote.context import Context
    from reemote.core1.call_get import CallGet

    async def callback(context: Context):
        return context.value + "World!"

    responses = await endpoint_execute(
        lambda: CallGet(
            callback=callback,
            value="Hello ",
            group="server104",
        )
    )
    assert len(responses) == 1
    for item in responses:
        assert item.value == "Hello World!"


@pytest.mark.asyncio
async def test_call_put(setup_inventory):
    from reemote.context import Context
    from reemote.core1.call_put import CallPut

    async def callback(context: Context):
        context.changed = context.value

    responses = await endpoint_execute(
        lambda: CallPut(
            callback=callback,
            value=True,
            group="server104",
        )
    )
    assert len(responses) == 1
    for item in responses:
        assert item.changed
    responses = await endpoint_execute(
        lambda: CallPut(
            callback=callback,
            value=False,
            group="server104",
        )
    )
    for item in responses:
        assert not item.changed


@pytest.mark.asyncio
async def test_call_post(setup_inventory):
    from reemote.context import Context
    from reemote.core1.call_post import CallPost

    async def callback(context: Context):
        context.changed = context.value

    responses = await endpoint_execute(
        lambda: CallPost(
            callback=callback,
            value=True,
            group="server104",
        )
    )
    assert len(responses) == 1
