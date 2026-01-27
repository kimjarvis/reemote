import pytest

from reemote.execute import endpoint_execute

@pytest.mark.asyncio
async def test_core_post_command_example(setup_inventory, setup_directory):
    from reemote.execute import execute
    from reemote import core

    responses = await endpoint_execute(lambda: core.post.Command(cmd='systemctl start firewalld'))

    print(responses[0].model_dump_json(indent=4))

    return responses
