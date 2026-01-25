import pytest

from reemote.execute import endpoint_execute

@pytest.mark.asyncio
async def test_get_context(setup_inventory):
    from reemote.execute import execute
    from reemote.context import Context
    from reemote import core1

    responses = await endpoint_execute(lambda: core1.get.Context())

    for response in responses:
        assert response.host in ["server104", "server105"]

    return responses