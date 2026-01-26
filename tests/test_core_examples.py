import pytest

from reemote.execute import endpoint_execute

# block insert examples/core/get/Call_test.generated
@pytest.mark.asyncio
async def test_core_get_call_example(setup_inventory, setup_directory):
    from reemote.execute import execute
    from reemote.context import Context
    from reemote import core

    async def callback(context: Context):
        return context.value + "World!"

    responses = await endpoint_execute(lambda: core.get.Call(callback=callback, value="Hello ", group="server108"))
    for item in responses:
        assert item.value == "Hello World!", "Expected the coroutine to yield 'World!' appended to the input value"

    return responses
# block end

# block insert examples/core/post/Call_test.generated
@pytest.mark.asyncio
async def test_core_post_call_example(setup_inventory, setup_directory):
    from reemote.execute import execute
    from reemote.context import Context
    from reemote import core

    async def callback(context: Context):
        # Make a change to the host
        pass

    responses = await endpoint_execute(lambda: core.post.Call(callback=callback, value="Hello", group="server108"))

    return responses
# block end

# block insert examples/core/put/Call_test.generated
@pytest.mark.asyncio
async def test_core_put_call_example(setup_inventory, setup_directory):
    from reemote.execute import execute
    from reemote.context import Context
    from reemote import core

    async def callback(context: Context):
        # Make a change to the host
        context.changed = True

    responses = await endpoint_execute(lambda: core.put.Call(callback=callback, value="Hello", group="server108"))
    for item in responses:
        assert item.changed == True, "Expected the coroutine to set the changed indicator"

    return responses
# block end

# block insert examples/core/get/Fact_test.generated
@pytest.mark.asyncio
async def test_core_get_fact_example(setup_inventory, setup_directory):
    from reemote.execute import execute
    from reemote import core

    responses = await endpoint_execute(lambda: core.get.Fact(cmd='echo Hello World!'))

    for item in responses:
        assert "Hello World" in item.value.stdout, "Expected the coroutine to yield the output of the command"

    return responses
# block end


# block insert examples/core/get/Context_test.generated
@pytest.mark.asyncio
async def test_core_get_context_example(setup_inventory, setup_directory):
    from reemote.execute import execute
    from reemote.context import Context
    from reemote import core

    responses = await endpoint_execute(lambda: core.get.Context())

    for response in responses:
        assert response.host in ["server108", "server105"]

    return responses
# block end

# block insert examples/core/get/Return_test.generated
@pytest.mark.asyncio
async def test_core_get_return_example(setup_inventory, setup_directory):
    from reemote import core
    from reemote.context import Context
    from reemote.execute import execute

    responses = await endpoint_execute(lambda: core.get.Return(value=1))
    assert all(response.value == 1 for response in responses), "Expected the coroutine to return the value"

    return responses
# block end

# block insert examples/core/post/Return_test.generated
@pytest.mark.asyncio
async def test_core_post_return_example(setup_inventory, setup_directory):
    from reemote import core
    from reemote.context import Context
    from reemote.execute import execute

    responses = await endpoint_execute(lambda: core.post.Return())

    return responses
# block end

# block insert examples/core/put/Return_test.generated
@pytest.mark.asyncio
async def test_core_put_return_example(setup_inventory, setup_directory):
    from reemote import core
    from reemote.context import Context
    from reemote.execute import execute

    responses = await endpoint_execute(lambda: core.put.Return(changed=True))
    assert all(response.changed for response in responses), "Expected the coroutine to return with changed equal to True"

    return responses
# block end


# block insert examples/core/post/Command_test.generated
@pytest.mark.asyncio
async def test_core_post_command_example(setup_inventory, setup_directory):
    from reemote.execute import execute
    from reemote import core

    responses = await endpoint_execute(lambda: core.post.Command(cmd='systemctl start firewalld'))

    return responses
# block end


