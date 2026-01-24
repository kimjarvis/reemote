import pytest

from reemote.execute import endpoint_execute


@pytest.mark.asyncio
async def test_get_call(setup_inventory):
    from reemote.context import Context
    from reemote.core1.get.Call import Call

    async def callback(context: Context):
        return context.value + "World!"

    responses = await endpoint_execute(
        lambda: Call(
            callback=callback,
            value="Hello ",
            group="server104",
        )
    )
    assert len(responses) == 1
    for item in responses:
        assert item.value == "Hello World!"


@pytest.mark.asyncio
async def test_put_call(setup_inventory):
    from reemote.context import Context
    from reemote.core1.put.Call import Call

    async def callback(context: Context):
        context.changed = context.value

    responses = await endpoint_execute(
        lambda: Call(
            callback=callback,
            value=True,
            group="server104",
        )
    )
    assert len(responses) == 1
    for item in responses:
        assert item.changed
    responses = await endpoint_execute(
        lambda: Call(
            callback=callback,
            value=False,
            group="server104",
        )
    )
    for item in responses:
        assert not item.changed


@pytest.mark.asyncio
async def test_post_call(setup_inventory):
    from reemote.context import Context
    from reemote.core1.post.Call import Call

    async def callback(context: Context):
        context.changed = context.value

    responses = await endpoint_execute(
        lambda: Call(
            callback=callback,
            value=True,
            group="server104",
        )
    )
    assert len(responses) == 1
