import pytest

from reemote.execute import endpoint_execute

# block insert examples/sftp/get/IsDir_test.generated
@pytest.mark.asyncio
async def test_sftp_get_IsDir_example(setup_inventory, setup_directory):
    from reemote.execute import execute
    from reemote import sftp1

    responses = await endpoint_execute(lambda: sftp1.get.IsDir(path=".."))
    for item in responses:
        assert item.value, (
            "Expected the coroutine to report that the current working directory exists on all hosts."
        )

    return responses
# block end

