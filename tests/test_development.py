import pytest

from reemote.execute import endpoint_execute

@pytest.mark.asyncio
async def test_core_get_fact_example(setup_inventory, setup_directory):
    from reemote.execute import execute
    from reemote import core

    responses = await endpoint_execute(lambda: core.get.Fact(cmd='echo Hello World!'))

    for item in responses:
        assert "Hello World" in item.value.stdout, "Expected the coroutine to yield the output of the command"

    print(responses[0].model_dump_json(indent=4))

    return responses
