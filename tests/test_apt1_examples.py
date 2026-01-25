import pytest

from reemote.execute import endpoint_execute

# block insert examples/apt/get/Packages_test.generated
@pytest.mark.asyncio
async def test_apt_get_packages_example(setup_inventory, setup_directory):
    from reemote.execute import execute
    from reemote import apt1

    responses = await endpoint_execute(lambda: apt1.get.Packages(cmd='echo Hello World!'))

    print(responses)

    return responses
# block end
